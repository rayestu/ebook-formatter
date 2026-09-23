from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Options:
    top_pct: float = 6.0        # % of page height excluded at top
    bottom_pct: float = 6.0     # % of page height excluded at bottom
    dehyphenate: bool = True
    footnotes: bool = True
    chapters: bool = True

    @classmethod
    def from_dict(cls, d) -> "Options":
        if not isinstance(d, dict):
            d = {}
        def f(k, default, alias):
            try:
                return max(0.0, min(15.0, float(d.get(k, d.get(alias, default)))))
            except (TypeError, ValueError):
                return default

        def b(k, default):
            v = d.get(k, default)
            if isinstance(v, str):
                return v.lower() in ("1", "true", "on", "yes")
            return bool(v)

        return cls(
            top_pct=f("top_pct", cls.top_pct, "top"),
            bottom_pct=f("bottom_pct", cls.bottom_pct, "bottom"),
            dehyphenate=b("dehyphenate", True),
            footnotes=b("footnotes", True),
            chapters=b("chapters", True),
        )

    def as_dict(self):
        return asdict(self)
