"""Load only the requested domain, keeping the agent edition independent."""
from importlib import import_module

__all__ = [
    "data_router",
    "model_router",
    "deployment_router",
    "iam_router",
    "observability_router",
    "platform_router",
    "iot_edge_router",
    "ops_router",
    "tools_router",
    "misc_router",
    "tensor_router",
    "ship_batch_router",
    "ship_samples_router",
    "coupling_clock_sync_router",
    "ice_uq_router",
]


def __getattr__(name):
    if name in __all__:
        router = import_module(f"api.domain.{name.removesuffix('_router')}").router
        globals()[name] = router
        return router
    raise AttributeError(name)
