import builtins

from lunar_nav.config import Config
from lunar_nav.datasets import make_provider


def test_make_provider_falls_back_to_synthetic_when_rasterio_missing(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rasterio" or name.startswith("rasterio."):
            raise ImportError("missing rasterio")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    cfg = Config(date="2027-03-15", demo=False)

    provider = make_provider(cfg)

    assert provider.name == "SYNTHETIC terrain (demo - not real Moon)"
