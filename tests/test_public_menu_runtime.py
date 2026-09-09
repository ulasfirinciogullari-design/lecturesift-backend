"""Small browser-free regression for the shared mobile disclosure menu."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_mobile_menu_close_paths_and_accessible_state():
    script = r'''
const fs = require('fs'), vm = require('vm');
const source = fs.readFileSync('frontend/site-shell.js','utf8');
const begin = source.indexOf('  const menuButton =');
const end = source.indexOf('  tools.append(nav);', begin);
if (begin < 0 || end < 0) throw Error('menu block missing');
const handlers = {}, buttonHandlers = {}, navHandlers = {};
const classes = new Set(); const attrs = {};
let focuses=0, mediaChange;
const menuButtonFake = {
  setAttribute(k,v){attrs[k]=v}, getAttribute(k){return attrs[k]},
  addEventListener(k,v){buttonHandlers[k]=v},focus(){focuses++}
};
const header={contains:n=>n.inside,classList:{toggle:(k,on)=>on?classes.add(k):classes.delete(k),contains:k=>classes.has(k)}};
const nav={id:'publicNavigation',addEventListener:(k,v)=>navHandlers[k]=v};
const document={createElement:()=>menuButtonFake,addEventListener:(k,v)=>handlers[k]=v};
const window={matchMedia:q=>({addEventListener:(k,v)=>mediaChange=v})};
vm.runInNewContext(source.slice(begin,end),{header,nav,document,window,tools:{append(){}},label:()=> 'Main menu'});
const state=()=>({expanded:attrs['aria-expanded'],open:classes.has('public-menu-open')});
const initial=state();buttonHandlers.click();const opened=state();
handlers.click({target:{inside:true}});const inside=state();
handlers.keydown({key:'Escape'});const escape=state();
buttonHandlers.click();handlers.click({target:{inside:false}});const outside=state();
buttonHandlers.click();navHandlers.click({target:{closest:()=>({})}});const navigated=state();
buttonHandlers.click();mediaChange();const resized=state();
console.log(JSON.stringify({initial,opened,inside,escape,outside,navigated,resized,focuses,attrs}));
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=5)
    states = json.loads(result.stdout)
    assert states["attrs"]["aria-controls"] == "publicNavigation"
    assert states["attrs"]["aria-label"] == "Main menu"
    assert states["opened"] == states["inside"] == {"expanded": "true", "open": True}
    for key in ("initial", "escape", "outside", "navigated", "resized"):
        assert states[key] == {"expanded": "false", "open": False}
    assert states["focuses"] == 1
