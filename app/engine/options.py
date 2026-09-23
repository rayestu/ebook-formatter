from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Options:
    top_pct: float = 6.0        # % of page height excluded at top (odd pages, if odd_even is set)
    bottom_pct: float = 6.0     # % of page height excluded at bottom (odd pages, if odd_even is set)
    odd_even: bool = False      # use a separate top/bottom zone for even-numbered pages
    top_pct_even: float = 6.0
    bottom_pct_even: float = 6.0
    dehyphenate: bool = True
    footnotes: bool = True
    chapters: bool = True

    def top_for(self, page_index: int) -> float:
        """page_index is 0-based; human page numbers (page_index + 1) are what "odd/even" means."""
        return self.top_pct_even if self.odd_even and (page_index + 1) % 2 == 0 else self.top_pct

    def bottom_for(self, page_index: int) -> float:
        return self.bottom_pct_even if self.odd_even and (page_index + 1) % 2 == 0 else self.bottom_pct

    @classmethod
    def from_dict(cls, d) -> "Options":
        if not isinstance(d, dict):
            d = {}
        def f(k, default, alias=None):
            try:
                return max(0.0, min(15.0, float(d.get(k, d.get(alias, default) if alias else default))))
            except (TypeError, ValueError):
                return default

        def b(k, default):
            v = d.get(k, default)
            if isinstance(v, str):
                return v.lower() in ("1", "true", "on", "yes")
            return bool(v)

        top = f("top_pct", cls.top_pct, "top")
        bottom = f("bottom_pct", cls.bottom_pct, "bottom")
        odd_even = b("odd_even", False)
        return cls(
            top_pct=top,
            bottom_pct=bottom,
            odd_even=odd_even,
            # default the even-page zone to the odd/first one when not supplied, so turning the
            # checkbox on for the first time starts from what's already on screen, not 6%/6%
            top_pct_even=f("top_pct_even", top, "top_even"),
            bottom_pct_even=f("bottom_pct_even", bottom, "bottom_even"),
            dehyphenate=b("dehyphenate", True),
            footnotes=b("footnotes", True),
            chapters=b("chapters", True),
        )

    def as_dict(self):
        return asdict(self)
