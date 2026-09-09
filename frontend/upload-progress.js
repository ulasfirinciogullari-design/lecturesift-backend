(function () {
  // Keep File/Blob parts lazy: large recordings must not be read into JS memory.
  // Known multipart byte ranges let one existing upload show each file's real
  // transfer progress, including separate audio/visual sources in the same job.
  function multipart(data) {
    const boundary = '----lecturesift' + crypto.randomUUID().replaceAll('-', '');
    const parts = [], files = [];
    let offset = 0;
    const append = value => {
      const part = typeof value === 'string' ? new TextEncoder().encode(value) : value;
      parts.push(part); offset += part.size ?? part.byteLength;
    };
    const quote = value => String(value).replaceAll('\r','%0D').replaceAll('\n','%0A').replaceAll('"','%22');
    for (const [name, value] of data.entries()) {
      append(`--${boundary}\r\nContent-Disposition: form-data; name="${quote(name)}"`);
      if (value instanceof Blob) {
        const filename = value.name || 'upload';
        const type = /^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/i.test(value.type) ? value.type : 'application/octet-stream';
        append(`; filename="${quote(filename)}"\r\nContent-Type: ${type}\r\n\r\n`);
        files.push({name:filename, field:name, size:value.size, start:offset, end:offset+value.size});
        append(value);
      } else {
        append(`\r\n\r\n${String(value).replace(/\r\n|\r|\n/g,'\r\n')}`);
      }
      append('\r\n');
    }
    append(`--${boundary}--\r\n`);
    const contentType = `multipart/form-data; boundary=${boundary}`;
    return {body:new Blob(parts), contentType, files};
  }
  function progress(files, transferred) {
    const loaded = Math.max(0, Number(transferred) || 0);
    return files.map(file => {
      const bytes = Math.max(0, Math.min(file.size, loaded - file.start));
      const complete = loaded >= file.end;
      // Avoid rounding up to 100 before the final byte has left the browser.
      return {...file, bytes, percent:complete ? 100 : file.size ? Math.min(99,Math.floor(bytes / file.size * 100)) : 0};
    });
  }
  window.LectureSiftUpload = {multipart, progress};
})();
