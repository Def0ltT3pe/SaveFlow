from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.risk.context import ServiceContext


@dataclass(frozen=True)
class FeatureResult:
    value: float
    reason: str | None = None


NO_SIGNAL = FeatureResult(0.0)


@dataclass(frozen=True)
class Feature:
    key: str
    title: str
    description: str
    default_weight: float
    compute: Callable[["ServiceContext"], FeatureResult]


FEATURES: dict[str, Feature] = {}


def feature(key: str, *, title: str, default_weight: float, description: str):
    """Декоратор регистрации признака. Ключ попадает в настройки модели как имя веса."""

    def register(fn: Callable[["ServiceContext"], FeatureResult]):
        if key in FEATURES:
            raise ValueError(f"Признак {key!r} уже зарегистрирован")
        FEATURES[key] = Feature(key, title, description, default_weight, fn)
        return fn

    return register


def all_features() -> dict[str, Feature]:
    from app.risk import features  # noqa: F401  (импорт регистрирует признаки)

    return FEATURES


def default_weights() -> dict[str, float]:
    return {key: f.default_weight for key, f in all_features().items()}
