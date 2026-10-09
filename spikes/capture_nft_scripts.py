import json, os
from jittersim.networking import renderer
_orig = renderer._render_nft_script
OUT = os.environ["NFT_CAPTURE"]
def wrapped(*a, **k):
    s = _orig(*a, **k)
    with open(OUT, "a") as f: f.write(json.dumps(s) + "\n")
    return s
renderer._render_nft_script = wrapped
