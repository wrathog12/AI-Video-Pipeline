"""The no-assets provider, and the default.

Not a placeholder. It is the reason every template is designed to look complete
with `assets: []`, which in turn is what lets the icon pack be genuinely optional:
a missing pack, an unmatched keyword and a deliberately icon-free theme all take
the same code path, so none of them is a special case that can break.

It also keeps the R7 claim honest. An interface with one implementation is not a
seam; `null` and `icon_pack` differing only in their return value is what makes
"swappable" demonstrable rather than asserted.
"""

from __future__ import annotations

from ..schema import AssetRef


class NullAssetProvider:
    name = "null"

    def resolve(self, keywords: list[str]) -> list[AssetRef]:  # noqa: ARG002
        return []
