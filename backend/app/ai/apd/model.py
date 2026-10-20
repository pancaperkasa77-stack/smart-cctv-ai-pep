from __future__ import annotations

import re

import torch

_RENAMES = (
    ("model.encoder.encoder.", "model.encoder.aifi."),
    (".self_attn.out_proj.", ".self_attn.o_proj."),
)
_FC_RE = re.compile(r"\.layers\.(\d+)\.fc([12])\.")


def remap_legacy_keys(state_dict: dict, expected_keys=None) -> dict:
    out = {}
    for original, value in state_dict.items():
        forward = original
        for old, new in _RENAMES:
            forward = forward.replace(old, new)
        forward = _FC_RE.sub(r".layers.\1.mlp.fc\2.", forward)
        reverse = original
        for old, new in _RENAMES:
            reverse = reverse.replace(new, old)
        reverse = re.sub(r"\.layers\.(\d+)\.mlp\.fc([12])\.", r".layers.\1.fc\2.", reverse)
        if expected_keys is None:
            key = forward
        else:
            matches = {k for k in (original, forward, reverse) if k in expected_keys}
            if len(matches) > 1:
                raise ValueError(f"Ambiguous APD parameter mapping: {original}")
            key = next(iter(matches)) if matches else original
        if key in out:
            raise ValueError(f"Duplicate APD parameter after mapping: {key}")
        out[key] = value
    return out


def load_model(weights: str, num_classes: int, id2label: dict, device: str = "cpu"):
    from transformers import RTDetrV2Config, RTDetrV2ForObjectDetection

    cfg = RTDetrV2Config(
        num_labels=num_classes,
        id2label=id2label,
        label2id={v: k for k, v in id2label.items()},
        use_pretrained_backbone=False,
    )
    model = RTDetrV2ForObjectDetection(cfg)

    from safetensors.torch import load_file
    if not str(weights).endswith(".safetensors"):
        raise ValueError("APD runtime accepts safetensors only")
    sd = load_file(str(weights), device="cpu")
    sd = remap_legacy_keys(sd, set(model.state_dict()))
    if not all(torch.isfinite(t).all() for t in sd.values()):
        raise ValueError("APD weights contain non-finite tensor values")

    missing, unexpected = model.load_state_dict(sd, strict=False)
    missing = [k for k in missing if "denoising" not in k]
    if missing or unexpected:
        raise RuntimeError(
            "Bobot tidak cocok dengan arsitektur.\n"
            f"  missing ({len(missing)}): {missing[:5]}\n"
            f"  unexpected ({len(unexpected)}): {unexpected[:5]}\n"
            "Kemungkinan versi transformers berubah lagi -- perbarui remap_legacy_keys()."
        )

    model.eval().to(device)
    return model


def build_processor(imgsz: int = 640):
    from transformers import RTDetrImageProcessor

    return RTDetrImageProcessor(
        do_resize=True,
        size={"height": imgsz, "width": imgsz},
        do_rescale=True,
        rescale_factor=1 / 255,
        do_normalize=False,
    )
