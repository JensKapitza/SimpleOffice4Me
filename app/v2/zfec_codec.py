"""Optional zfec integration for V2 k-of-n erasure coding."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .fragments import ErasureCodec, FragmentPlan, FragmentRecoveryError


def _load_backend() -> tuple[type[Any], type[Any]]:
    try:
        from zfec.easyfec import Decoder, Encoder
    except ImportError as exc:
        raise FragmentRecoveryError(
            "zfec support is not installed; install the optional 'erasure' extra"
        ) from exc
    return Encoder, Decoder


class ZfecErasureCodec:
    """Adapter around zfec.easyfec with a stable persisted codec contract."""

    name = "zfec"
    version = "1"

    @staticmethod
    def _validate_plan(plan: FragmentPlan) -> None:
        if plan.codec != ZfecErasureCodec.name or plan.codec_version != ZfecErasureCodec.version:
            raise FragmentRecoveryError("zfec adapter does not match the requested codec contract")

    def encode(self, payload: bytes, plan: FragmentPlan) -> Sequence[bytes]:
        self._validate_plan(plan)
        content = bytes(payload)
        if not content:
            return tuple(b"" for _ in range(plan.n))
        Encoder, _ = _load_backend()
        try:
            fragments = tuple(bytes(item) for item in Encoder(plan.k, plan.n).encode(content))
        except Exception as exc:
            raise FragmentRecoveryError("zfec encoding failed") from exc
        if len(fragments) != plan.n:
            raise FragmentRecoveryError("zfec returned an unexpected fragment count")
        if len({len(item) for item in fragments}) != 1:
            raise FragmentRecoveryError("zfec returned unequal fragment sizes")
        return fragments

    def decode(
        self,
        fragments: Mapping[int, bytes],
        plan: FragmentPlan,
        *,
        original_size: int,
        padding: int,
    ) -> bytes:
        self._validate_plan(plan)
        if original_size < 0 or padding < 0:
            raise FragmentRecoveryError("invalid zfec recovery size metadata")
        ordered = sorted((int(index), bytes(payload)) for index, payload in fragments.items())
        if len(ordered) < plan.k:
            raise FragmentRecoveryError("zfec recovery requires at least k valid fragments")
        if any(index < 0 or index >= plan.n for index, _ in ordered):
            raise FragmentRecoveryError("zfec fragment index is outside the recovery plan")
        selected = ordered[: plan.k]
        if original_size == 0:
            if padding != 0 or any(payload for _, payload in selected):
                raise FragmentRecoveryError("invalid empty zfec recovery metadata")
            return b""

        sizes = {len(payload) for _, payload in selected}
        if len(sizes) != 1:
            raise FragmentRecoveryError("zfec recovery fragments have unequal sizes")
        fragment_size = next(iter(sizes))
        if fragment_size * plan.k - original_size != padding:
            raise FragmentRecoveryError("zfec padding metadata does not match fragment size")

        _, Decoder = _load_backend()
        try:
            decoded = Decoder(plan.k, plan.n).decode(
                [payload for _, payload in selected],
                [index for index, _ in selected],
                padding,
            )
        except Exception as exc:
            raise FragmentRecoveryError("zfec decoding failed") from exc
        return bytes(decoded)


def codec_for_plan(plan: FragmentPlan) -> ErasureCodec:
    if plan.codec == ZfecErasureCodec.name and plan.codec_version == ZfecErasureCodec.version:
        return ZfecErasureCodec()
    raise FragmentRecoveryError(
        f"unsupported erasure codec {plan.codec}/{plan.codec_version}"
    )
