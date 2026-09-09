from types import SimpleNamespace

import pytest

from lecturesift import ai, media
from lecturesift.errors import LectureSiftError


@pytest.mark.parametrize("dictionary_usage", [False, True])
def test_output_limit_retranscribes_smaller_chunks_in_source_order(tmp_path, monkeypatch, dictionary_usage):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"synthetic-provider-fixture")
    calls, billed = [], []

    def create(**arguments):
        name = arguments["file"].name
        calls.append(name)
        text, tokens = ("Incomplete original", 2000) if len(calls) == 1 else (
            ("Beginning of the lesson", 300) if len(calls) == 2 else ("Final chapter about whales", 200)
        )
        usage = {"output_tokens": tokens} if dictionary_usage else SimpleNamespace(output_tokens=tokens)
        return SimpleNamespace(text=text, usage=usage)

    def split(path, directory, **options):
        assert path == source and options["segment_seconds"] == 60
        parts = [directory / "first.mp3", directory / "last.mp3"]
        for part in parts:
            part.write_bytes(b"synthetic-provider-fixture")
        return parts

    monkeypatch.setattr(ai, "_CLIENT", SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))))
    monkeypatch.setattr(ai, "record_openai_response", lambda *args: billed.append(args) or True)
    monkeypatch.setattr(media, "extract_audio_chunks", split)
    assert ai.transcribe(source, "en", 120) == "Beginning of the lesson\nFinal chapter about whales"
    assert len(calls) == len(billed) == 3
    assert not list(tmp_path.glob("transcript-retry-*"))
    assert source.exists()


def test_output_limit_at_minimum_chunk_fails_without_unbounded_retry(tmp_path, monkeypatch):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"synthetic-provider-fixture")
    calls = []

    def create(**arguments):
        calls.append(arguments)
        return SimpleNamespace(text="Incomplete", usage=SimpleNamespace(output_tokens=1900))

    monkeypatch.setattr(ai, "_CLIENT", SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))))
    monkeypatch.setattr(ai, "record_openai_response", lambda *_args: True)
    with pytest.raises(LectureSiftError) as error:
        ai.transcribe(source, "en", 15)
    assert error.value.code == "LS-AI-08"
    assert len(calls) == 1
