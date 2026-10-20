"""Konversi state dictionary APD tepercaya menjadi Safetensors, offline.

Jalankan dalam container sekali pakai tanpa jaringan dengan Torch >=2.6.
Runtime aplikasi tidak pernah memanggil torch.load.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output sudah ada; tidak ditimpa")
    if not args.source.is_file() or args.source.stat().st_size > 512 * 1024 * 1024:
        parser.error("checkpoint tidak tersedia atau melebihi 512 MiB")
    with args.source.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if digest != args.expected_sha256.lower():
        parser.error("SHA256 berbeda dari checkpoint yang diperiksa")

    import torch
    from packaging.version import Version
    from safetensors.torch import save_file

    if Version(torch.__version__.split("+")[0]) < Version("2.6.0"):
        raise RuntimeError("Konversi memerlukan Torch >=2.6; jangan gunakan unrestricted pickle")
    torch.set_num_threads(2)
    state = torch.load(args.source, map_location="cpu", weights_only=True)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    if not isinstance(state, dict) or not 1 <= len(state) <= 10000:
        raise ValueError("Checkpoint bukan state dictionary yang didukung")
    converted = {}
    total = 0
    for name, tensor in state.items():
        if not isinstance(name, str) or not isinstance(tensor, torch.Tensor):
            raise ValueError("State dictionary hanya boleh berisi nama dan tensor")
        if tensor.layout != torch.strided or tensor.numel() > 100_000_000:
            raise ValueError("Layout/ukuran tensor tidak didukung")
        total += tensor.numel() * tensor.element_size()
        if total > 512 * 1024 * 1024 or not torch.isfinite(tensor).all():
            raise ValueError("Ukuran/nilai tensor tidak valid")
        converted[name] = tensor.detach().cpu().contiguous().clone()
    save_file(converted, str(args.output), metadata={"source_sha256": digest, "format": "pt"})
    print(json.dumps({"output": str(args.output), "tensors": len(converted),
                      "tensor_bytes": total, "source_sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
