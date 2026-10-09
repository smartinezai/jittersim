Spike, not for merge. Run inside the jittersim-kernel-tests image with NET_ADMIN and python3-nftables:
1. PYTHONPATH=src:spikes NFT_CAPTURE=scripts.jsonl pytest -p capture_nft_scripts tests/unit/test_renderer.py ...
2. docker run --cap-add NET_ADMIN ... python3 spikes/nft_replay_python_nftables.py scripts.jsonl
Result: 47 distinct renderer scripts apply to identical kernel rulesets through nft -f - and python-nftables.
