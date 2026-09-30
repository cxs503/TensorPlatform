"""Deterministic, bounded PyTorch adapters for model-factory experiments.

The adapters in this module intentionally do not know about HTTP, SQLAlchemy or
Celery.  The experiment runtime passes an authorised dataset path (if any) and
an isolated work directory through a duck-typed context object.

No adapter silently substitutes generated data.  A caller must either provide
``context.dataset_path`` or set ``context.config["fixture_mode"]`` to ``True``.
Fixtures are deliberately small and deterministic so the platform can prove
that its training path works before production data is connected.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np


class ExperimentAlgorithmError(ValueError):
    """Raised when an experiment request cannot be executed safely."""


class AlgorithmUnavailableError(RuntimeError):
    """Raised when the platform runtime does not expose the required engine."""


ProgressCallback = Callable[[float, Mapping[str, float]], None]


def _require_torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - exercised on minimal dev hosts
        raise AlgorithmUnavailableError(
            "PyTorch is unavailable in this process; run the experiment in the "
            "platform backend/worker image that already contains PyTorch"
        ) from exc
    return torch


def _context_config(context: Any) -> dict[str, Any]:
    raw = getattr(context, "config", None)
    if not isinstance(raw, Mapping):
        raise ExperimentAlgorithmError("context.config must be a mapping")
    return dict(raw)


def _select_source(context: Any, config: Mapping[str, Any]) -> tuple[str, Path | None]:
    fixture_mode = config.get("fixture_mode", False)
    if not isinstance(fixture_mode, bool):
        raise ExperimentAlgorithmError("fixture_mode must be a boolean")

    raw_path = getattr(context, "dataset_path", None)
    if fixture_mode and raw_path:
        raise ExperimentAlgorithmError(
            "fixture_mode and dataset_path are mutually exclusive"
        )
    if not fixture_mode and not raw_path:
        raise ExperimentAlgorithmError(
            "an authorised dataset_path or fixture_mode=true is required"
        )
    if fixture_mode:
        return "fixture", None

    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise ExperimentAlgorithmError(f"dataset does not exist: {path}")
    return "dataset", path


def _work_dir(context: Any) -> Path:
    raw = getattr(context, "work_dir", None)
    if not raw:
        raise ExperimentAlgorithmError("context.work_dir is required")
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ExperimentAlgorithmError(f"work_dir is not a directory: {path}")
    return path


def _integer(
    config: Mapping[str, Any], name: str, default: int, minimum: int, maximum: int
) -> int:
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExperimentAlgorithmError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ExperimentAlgorithmError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _number(
    config: Mapping[str, Any], name: str, default: float, minimum: float, maximum: float
) -> float:
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentAlgorithmError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ExperimentAlgorithmError(
            f"{name} must be finite and between {minimum} and {maximum}"
        )
    return result


def _emit_progress(context: Any, fraction: float, metrics: Mapping[str, float]) -> None:
    callback: ProgressCallback | None = getattr(context, "progress", None)
    if callback is None:
        return
    if not callable(callback):
        raise ExperimentAlgorithmError("context.progress must be callable")
    callback(float(max(0.0, min(1.0, fraction))), dict(metrics))


def _finite_metrics(metrics: Mapping[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for name, value in metrics.items():
        scalar = float(value)
        if not math.isfinite(scalar):
            raise ExperimentAlgorithmError(f"non-finite metric produced: {name}")
        result[name] = scalar
    return result


def _save_artifact(torch: Any, payload: Mapping[str, Any], path: Path) -> dict[str, Any]:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        torch.save(dict(payload), temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "format": "pytorch_state_dict",
        "sha256": digest,
        "size_bytes": path.stat().st_size,
    }


def _mask_from_npz(raw: Any, size: int, name: str) -> np.ndarray:
    array = np.asarray(raw)
    if array.dtype == np.bool_:
        if array.ndim != 1 or len(array) != size:
            raise ExperimentAlgorithmError(f"{name} boolean mask must have {size} rows")
        return array.copy()
    if array.ndim != 1 or not np.issubdtype(array.dtype, np.integer):
        raise ExperimentAlgorithmError(f"{name} must be a boolean mask or integer indices")
    if len(array) == 0 or np.any(array < 0) or np.any(array >= size):
        raise ExperimentAlgorithmError(f"{name} contains invalid indices")
    mask = np.zeros(size, dtype=bool)
    mask[array.astype(np.int64)] = True
    return mask


def _gnn_fixture() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    # Two connected communities with deterministic features and a bridge.
    features = np.asarray(
        [
            [1.0, 0.0, 0.0], [0.9, 0.1, 0.1], [1.0, 0.0, 0.2],
            [0.8, 0.2, 0.3], [0.9, 0.1, 0.4], [0.0, 1.0, 0.0],
            [0.1, 0.9, 0.1], [0.0, 1.0, 0.2], [0.2, 0.8, 0.3],
            [0.1, 0.9, 0.4],
        ],
        dtype=np.float32,
    )
    labels = np.asarray([0] * 5 + [1] * 5, dtype=np.int64)
    undirected = [
        (0, 1), (1, 2), (2, 3), (3, 4), (4, 0), (0, 2),
        (5, 6), (6, 7), (7, 8), (8, 9), (9, 5), (5, 7), (4, 5),
    ]
    edges = np.asarray(undirected, dtype=np.int64).T
    train_mask = np.asarray([True, True, True, False, False] * 2)
    test_mask = ~train_mask
    return features, labels, edges, train_mask, test_mask


def _load_gnn_npz(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if path.suffix.lower() != ".npz":
        raise ExperimentAlgorithmError(
            "GNN datasets must be .npz files with features, labels, edges, "
            "train_mask and test_mask arrays"
        )
    try:
        with np.load(path, allow_pickle=False) as data:
            required = {"features", "labels", "edges", "train_mask", "test_mask"}
            missing = required.difference(data.files)
            if missing:
                raise ExperimentAlgorithmError(
                    f"GNN dataset is missing arrays: {', '.join(sorted(missing))}"
                )
            features = np.asarray(data["features"], dtype=np.float32)
            labels = np.asarray(data["labels"])
            edges = np.asarray(data["edges"])
            train_raw = np.asarray(data["train_mask"])
            test_raw = np.asarray(data["test_mask"])
    except ExperimentAlgorithmError:
        raise
    except Exception as exc:
        raise ExperimentAlgorithmError(f"cannot read GNN NPZ dataset: {exc}") from exc

    if features.ndim != 2 or features.shape[0] < 4 or features.shape[1] < 1:
        raise ExperimentAlgorithmError("features must be a 2D array with at least 4 nodes")
    if not np.isfinite(features).all():
        raise ExperimentAlgorithmError("features contain non-finite values")
    if labels.ndim != 1 or len(labels) != len(features):
        raise ExperimentAlgorithmError("labels must contain one value per node")
    if not np.issubdtype(labels.dtype, np.integer):
        raise ExperimentAlgorithmError("labels must be integers")
    labels = labels.astype(np.int64)
    unique = np.unique(labels)
    if len(unique) < 2 or not np.array_equal(unique, np.arange(len(unique))):
        raise ExperimentAlgorithmError("labels must be contiguous integers starting at zero")
    if edges.ndim != 2 or edges.shape[0] != 2 or edges.shape[1] < 1:
        raise ExperimentAlgorithmError("edges must have shape [2, edge_count]")
    if not np.issubdtype(edges.dtype, np.integer):
        raise ExperimentAlgorithmError("edges must contain integer node indices")
    edges = edges.astype(np.int64)
    if np.any(edges < 0) or np.any(edges >= len(features)):
        raise ExperimentAlgorithmError("edges contain an out-of-range node index")
    train_mask = _mask_from_npz(train_raw, len(features), "train_mask")
    test_mask = _mask_from_npz(test_raw, len(features), "test_mask")
    if not train_mask.any() or not test_mask.any() or np.any(train_mask & test_mask):
        raise ExperimentAlgorithmError("train_mask and test_mask must be non-empty and disjoint")
    if len(np.unique(labels[train_mask])) < len(unique):
        raise ExperimentAlgorithmError("training mask must include every label class")
    return features, labels, edges, train_mask, test_mask


def _validate_gnn_size(features: np.ndarray, edges: np.ndarray) -> None:
    if len(features) > 5_000:
        raise ExperimentAlgorithmError("GNN node count exceeds the 5000-node limit")
    if features.shape[1] > 2_048:
        raise ExperimentAlgorithmError("GNN feature count exceeds the 2048-feature limit")
    if edges.shape[1] > 100_000:
        raise ExperimentAlgorithmError("GNN edge count exceeds the 100000-edge limit")


def _normalised_adjacency(torch: Any, edges: Any, node_count: int):
    adjacency = torch.zeros((node_count, node_count), dtype=torch.float32, device="cpu")
    adjacency[edges[0], edges[1]] = 1.0
    adjacency[edges[1], edges[0]] = 1.0
    adjacency.fill_diagonal_(1.0)
    degree = adjacency.sum(dim=1).clamp_min(1.0)
    inv_sqrt = degree.pow(-0.5)
    return inv_sqrt[:, None] * adjacency * inv_sqrt[None, :]


def _build_gcn(torch: Any, feature_count: int, hidden_dim: int, class_count: int):
    class GCN(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.input_layer = torch.nn.Linear(feature_count, hidden_dim)
            self.output_layer = torch.nn.Linear(hidden_dim, class_count)

        def forward(self, features, adjacency):
            hidden = adjacency @ features
            hidden = torch.relu(self.input_layer(hidden))
            hidden = adjacency @ hidden
            return self.output_layer(hidden)

    return GCN()


def _load_torch_artifact(torch: Any, artifact_path: str | Path, expected_kind: str):
    path = Path(artifact_path).expanduser().resolve()
    if not path.is_file():
        raise ExperimentAlgorithmError(f"model artifact does not exist: {path}")
    try:
        payload = torch.load(path, map_location="cpu")
    except Exception as exc:
        raise ExperimentAlgorithmError(f"cannot load model artifact: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("kind") != expected_kind:
        raise ExperimentAlgorithmError(
            f"artifact is not a completed {expected_kind} training artifact"
        )
    return payload


def run_gnn(context: Any) -> dict[str, Any]:
    """Train a small real GCN node classifier and persist its state dict."""

    config = _context_config(context)
    source, dataset_path = _select_source(context, config)
    epochs = _integer(config, "epochs", 60, 1, 200)
    hidden_dim = _integer(config, "hidden_dim", 16, 2, 128)
    seed = _integer(config, "seed", 17, 0, 2_147_483_647)
    learning_rate = _number(config, "learning_rate", 0.03, 0.00001, 1.0)
    weight_decay = _number(config, "weight_decay", 0.0005, 0.0, 1.0)

    arrays = _gnn_fixture() if source == "fixture" else _load_gnn_npz(dataset_path)
    features_np, labels_np, edges_np, train_mask_np, test_mask_np = arrays
    _validate_gnn_size(features_np, edges_np)

    torch = _require_torch()
    torch.manual_seed(seed)
    features = torch.as_tensor(features_np, dtype=torch.float32, device="cpu")
    labels = torch.as_tensor(labels_np, dtype=torch.long, device="cpu")
    edges = torch.as_tensor(edges_np, dtype=torch.long, device="cpu")
    train_mask = torch.as_tensor(train_mask_np, dtype=torch.bool, device="cpu")
    test_mask = torch.as_tensor(test_mask_np, dtype=torch.bool, device="cpu")
    adjacency = _normalised_adjacency(torch, edges, len(features_np))

    class_count = int(labels.max().item()) + 1
    model = _build_gcn(torch, features.shape[1], hidden_dim, class_count).to("cpu")
    optimizer = torch.optim.Adam(
        model.parameters(), lr=learning_rate, weight_decay=weight_decay
    )
    loss_fn = torch.nn.CrossEntropyLoss()
    report_every = max(1, epochs // 10)

    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        logits = model(features, adjacency)
        loss = loss_fn(logits[train_mask], labels[train_mask])
        loss.backward()
        optimizer.step()
        if epoch == 0 or (epoch + 1) % report_every == 0 or epoch + 1 == epochs:
            _emit_progress(context, (epoch + 1) / epochs, {"train_loss": loss.item()})

    model.eval()
    with torch.no_grad():
        logits = model(features, adjacency)
        final_loss = loss_fn(logits[train_mask], labels[train_mask]).item()
        predictions = logits.argmax(dim=1)
        train_accuracy = (predictions[train_mask] == labels[train_mask]).float().mean().item()
        test_accuracy = (predictions[test_mask] == labels[test_mask]).float().mean().item()
    metrics = _finite_metrics(
        {
            "train_loss": final_loss,
            "train_accuracy": train_accuracy,
            "test_accuracy": test_accuracy,
        }
    )

    artifact = _save_artifact(
        torch,
        {
            "kind": "gnn",
            "model_state_dict": model.state_dict(),
            "model_config": {
                "feature_count": int(features.shape[1]),
                "hidden_dim": hidden_dim,
                "class_count": class_count,
            },
            "training_config": {
                "epochs": epochs,
                "learning_rate": learning_rate,
                "weight_decay": weight_decay,
                "seed": seed,
            },
            "metrics": metrics,
            "inference_graph": {
                "features": features.detach().cpu(),
                "edges": edges.detach().cpu(),
                "node_names": [f"node_{index}" for index in range(len(features_np))],
            },
        },
        _work_dir(context) / "gnn_model.pt",
    )
    return {
        "kind": "gnn",
        "status": "completed",
        "metrics": metrics,
        "artifact_path": artifact["path"],
        "artifact": artifact,
        "model": {
            "name": str(config.get("model_name") or "gnn-node-classifier"),
            "model_type": "gnn_node_classifier",
            "framework": "pytorch",
            "framework_version": str(torch.__version__),
        },
        "metadata": {
            "data_source": source,
            "node_count": int(features.shape[0]),
            "edge_count": int(edges.shape[1]),
            "feature_count": int(features.shape[1]),
            "class_count": class_count,
            "device": "cpu",
        },
    }


def predict_gnn_from_artifact(
    artifact_path: str | Path, nodes: list[str | int] | tuple[str | int, ...]
) -> dict[str, Any]:
    """Run node inference using only a completed GNN training artifact."""

    if not isinstance(nodes, (list, tuple)) or not nodes:
        raise ExperimentAlgorithmError("nodes must be a non-empty list")
    if len(nodes) > 1_000:
        raise ExperimentAlgorithmError("node request exceeds the 1000-node limit")
    torch = _require_torch()
    payload = _load_torch_artifact(torch, artifact_path, "gnn")
    model_config = payload.get("model_config")
    graph = payload.get("inference_graph")
    state_dict = payload.get("model_state_dict")
    if not isinstance(model_config, Mapping) or not isinstance(graph, Mapping):
        raise ExperimentAlgorithmError("GNN artifact is missing inference metadata")
    if not isinstance(state_dict, Mapping):
        raise ExperimentAlgorithmError("GNN artifact is missing trained weights")

    feature_count = int(model_config.get("feature_count", 0))
    hidden_dim = int(model_config.get("hidden_dim", 0))
    class_count = int(model_config.get("class_count", 0))
    if not 1 <= feature_count <= 2_048 or not 2 <= hidden_dim <= 128:
        raise ExperimentAlgorithmError("GNN artifact contains invalid model dimensions")
    if not 2 <= class_count <= 10_000:
        raise ExperimentAlgorithmError("GNN artifact contains an invalid class count")

    features = torch.as_tensor(graph.get("features"), dtype=torch.float32, device="cpu")
    edges = torch.as_tensor(graph.get("edges"), dtype=torch.long, device="cpu")
    node_names = graph.get("node_names")
    if features.ndim != 2 or features.shape[1] != feature_count:
        raise ExperimentAlgorithmError("GNN artifact contains invalid inference features")
    if len(features) > 5_000 or edges.ndim != 2 or edges.shape[0] != 2:
        raise ExperimentAlgorithmError("GNN artifact contains an invalid inference graph")
    if not isinstance(node_names, list) or len(node_names) != len(features):
        raise ExperimentAlgorithmError("GNN artifact contains invalid node names")
    if len(set(node_names)) != len(node_names):
        raise ExperimentAlgorithmError("GNN artifact contains duplicate node names")
    name_to_index = {name: index for index, name in enumerate(node_names)}

    selected: list[tuple[str | int, int]] = []
    for node in nodes:
        if isinstance(node, bool):
            raise ExperimentAlgorithmError(f"unknown GNN node: {node}")
        if isinstance(node, int):
            index = node
        elif isinstance(node, str) and node in name_to_index:
            index = name_to_index[node]
        else:
            raise ExperimentAlgorithmError(f"unknown GNN node: {node}")
        if not 0 <= index < len(features):
            raise ExperimentAlgorithmError(f"unknown GNN node: {node}")
        selected.append((node, index))

    model = _build_gcn(torch, feature_count, hidden_dim, class_count).to("cpu")
    try:
        model.load_state_dict(state_dict, strict=True)
    except Exception as exc:
        raise ExperimentAlgorithmError(f"GNN artifact weights do not match metadata: {exc}") from exc
    adjacency = _normalised_adjacency(torch, edges, len(features))
    model.eval()
    with torch.no_grad():
        probabilities = torch.softmax(model(features, adjacency), dim=1)
    predictions = []
    for requested, index in selected:
        row = probabilities[index]
        predictions.append(
            {
                "node": requested,
                "node_index": index,
                "class_id": int(row.argmax().item()),
                "confidence": float(row.max().item()),
            }
        )
    return {
        "kind": "gnn_prediction",
        "predictions": predictions,
        "model_type": "gnn_node_classifier",
    }


def _gan_fixture() -> np.ndarray:
    # Fixed two-mode 2D distribution; no generated fallback is used implicitly.
    angle = np.linspace(0.0, 2.0 * np.pi, 128, endpoint=False, dtype=np.float32)
    first = np.column_stack((np.cos(angle) * 0.35 - 1.0, np.sin(angle) * 0.2))
    second = np.column_stack((np.cos(angle) * 0.35 + 1.0, np.sin(angle) * 0.2))
    return np.concatenate((first, second), axis=0).astype(np.float32)


def _load_gan_npz(path: Path) -> np.ndarray:
    if path.suffix.lower() != ".npz":
        raise ExperimentAlgorithmError("GAN datasets must be .npz files with a samples array")
    try:
        with np.load(path, allow_pickle=False) as data:
            if "samples" not in data.files:
                raise ExperimentAlgorithmError("GAN dataset is missing the samples array")
            samples = np.asarray(data["samples"], dtype=np.float32)
    except ExperimentAlgorithmError:
        raise
    except Exception as exc:
        raise ExperimentAlgorithmError(f"cannot read GAN NPZ dataset: {exc}") from exc
    if samples.ndim != 2 or samples.shape[0] < 8 or samples.shape[1] < 1:
        raise ExperimentAlgorithmError("samples must be 2D with at least 8 rows")
    if samples.shape[0] > 50_000:
        raise ExperimentAlgorithmError("GAN sample count exceeds the 50000-row limit")
    if samples.shape[1] > 128:
        raise ExperimentAlgorithmError("GAN feature count exceeds the 128-feature limit")
    if not np.isfinite(samples).all():
        raise ExperimentAlgorithmError("GAN samples contain non-finite values")
    return samples


def _build_gan(torch: Any, latent_dim: int, hidden_dim: int, output_dim: int):
    generator = torch.nn.Sequential(
        torch.nn.Linear(latent_dim, hidden_dim),
        torch.nn.LeakyReLU(0.2),
        torch.nn.Linear(hidden_dim, hidden_dim),
        torch.nn.LeakyReLU(0.2),
        torch.nn.Linear(hidden_dim, output_dim),
    )
    discriminator = torch.nn.Sequential(
        torch.nn.Linear(output_dim, hidden_dim),
        torch.nn.LeakyReLU(0.2),
        torch.nn.Linear(hidden_dim, hidden_dim),
        torch.nn.LeakyReLU(0.2),
        torch.nn.Linear(hidden_dim, 1),
    )
    return generator.to("cpu"), discriminator.to("cpu")


def run_gan(context: Any) -> dict[str, Any]:
    """Train a bounded real GAN and persist generator/discriminator state dicts."""

    config = _context_config(context)
    source, dataset_path = _select_source(context, config)
    epochs = _integer(config, "epochs", 80, 1, 500)
    latent_dim = _integer(config, "latent_dim", 8, 2, 64)
    hidden_dim = _integer(config, "hidden_dim", 32, 4, 256)
    batch_size = _integer(config, "batch_size", 64, 8, 512)
    seed = _integer(config, "seed", 23, 0, 2_147_483_647)
    learning_rate = _number(config, "learning_rate", 0.001, 0.000001, 0.1)

    samples_np = _gan_fixture() if source == "fixture" else _load_gan_npz(dataset_path)
    torch = _require_torch()
    torch.manual_seed(seed)
    samples = torch.as_tensor(samples_np, dtype=torch.float32, device="cpu")
    generator, discriminator = _build_gan(
        torch, latent_dim, hidden_dim, int(samples.shape[1])
    )
    generator_optimizer = torch.optim.Adam(
        generator.parameters(), lr=learning_rate, betas=(0.5, 0.999)
    )
    discriminator_optimizer = torch.optim.Adam(
        discriminator.parameters(), lr=learning_rate, betas=(0.5, 0.999)
    )
    loss_fn = torch.nn.BCEWithLogitsLoss()
    sample_count = int(samples.shape[0])
    report_every = max(1, epochs // 10)
    final_generator_loss = final_discriminator_loss = 0.0

    for epoch in range(epochs):
        # Seeded randperm and noise make each run repeatable without fake metrics.
        permutation = torch.randperm(sample_count, device="cpu")
        epoch_generator_loss = 0.0
        epoch_discriminator_loss = 0.0
        batches = 0
        for start in range(0, sample_count, batch_size):
            real = samples[permutation[start : start + batch_size]]
            current_size = int(real.shape[0])
            if current_size == 0:
                continue

            discriminator_optimizer.zero_grad(set_to_none=True)
            noise = torch.randn((current_size, latent_dim), device="cpu")
            fake = generator(noise)
            real_logits = discriminator(real)
            fake_logits = discriminator(fake.detach())
            discriminator_loss = loss_fn(real_logits, torch.ones_like(real_logits))
            discriminator_loss += loss_fn(fake_logits, torch.zeros_like(fake_logits))
            discriminator_loss.backward()
            discriminator_optimizer.step()

            generator_optimizer.zero_grad(set_to_none=True)
            noise = torch.randn((current_size, latent_dim), device="cpu")
            generated = generator(noise)
            generated_logits = discriminator(generated)
            generator_loss = loss_fn(generated_logits, torch.ones_like(generated_logits))
            generator_loss.backward()
            generator_optimizer.step()

            epoch_discriminator_loss += float(discriminator_loss.item())
            epoch_generator_loss += float(generator_loss.item())
            batches += 1
        if batches == 0:  # defensive: input validation should make this unreachable
            raise ExperimentAlgorithmError("GAN training produced no batches")
        final_generator_loss = epoch_generator_loss / batches
        final_discriminator_loss = epoch_discriminator_loss / batches
        if epoch == 0 or (epoch + 1) % report_every == 0 or epoch + 1 == epochs:
            _emit_progress(
                context,
                (epoch + 1) / epochs,
                {
                    "generator_loss": final_generator_loss,
                    "discriminator_loss": final_discriminator_loss,
                },
            )

    generator.eval()
    discriminator.eval()
    with torch.no_grad():
        evaluation_noise = torch.randn((min(sample_count, 512), latent_dim), device="cpu")
        generated = generator(evaluation_noise)
        real_score = torch.sigmoid(discriminator(samples[: len(generated)])).mean().item()
        fake_score = torch.sigmoid(discriminator(generated)).mean().item()
    metrics = _finite_metrics(
        {
            "generator_loss": final_generator_loss,
            "discriminator_loss": final_discriminator_loss,
            "real_score": real_score,
            "fake_score": fake_score,
        }
    )

    artifact = _save_artifact(
        torch,
        {
            "kind": "gan",
            "generator_state_dict": generator.state_dict(),
            "discriminator_state_dict": discriminator.state_dict(),
            "model_config": {
                "latent_dim": latent_dim,
                "hidden_dim": hidden_dim,
                "output_dim": int(samples.shape[1]),
            },
            "training_config": {
                "epochs": epochs,
                "batch_size": batch_size,
                "learning_rate": learning_rate,
                "seed": seed,
            },
            "metrics": metrics,
        },
        _work_dir(context) / "gan_model.pt",
    )
    return {
        "kind": "gan",
        "status": "completed",
        "metrics": metrics,
        "artifact_path": artifact["path"],
        "artifact": artifact,
        "model": {
            "name": str(config.get("model_name") or "gan-generator"),
            "model_type": "generative_adversarial_network",
            "framework": "pytorch",
            "framework_version": str(torch.__version__),
        },
        "metadata": {
            "data_source": source,
            "sample_count": sample_count,
            "feature_count": int(samples.shape[1]),
            "device": "cpu",
        },
    }


def generate_gan_from_artifact(
    artifact_path: str | Path, count: int, seed: int = 0
) -> dict[str, Any]:
    """Generate numeric samples from trained GAN weights, never a fresh model."""

    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1_000:
        raise ExperimentAlgorithmError("count must be an integer between 1 and 1000")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2_147_483_647:
        raise ExperimentAlgorithmError("seed must be an integer between 0 and 2147483647")
    torch = _require_torch()
    payload = _load_torch_artifact(torch, artifact_path, "gan")
    model_config = payload.get("model_config")
    state_dict = payload.get("generator_state_dict")
    if not isinstance(model_config, Mapping) or not isinstance(state_dict, Mapping):
        raise ExperimentAlgorithmError("GAN artifact is missing trained generator data")
    latent_dim = int(model_config.get("latent_dim", 0))
    hidden_dim = int(model_config.get("hidden_dim", 0))
    output_dim = int(model_config.get("output_dim", 0))
    if not 2 <= latent_dim <= 64 or not 4 <= hidden_dim <= 256:
        raise ExperimentAlgorithmError("GAN artifact contains invalid model dimensions")
    if not 1 <= output_dim <= 128:
        raise ExperimentAlgorithmError("GAN artifact contains an invalid output dimension")

    # Strict loading occurs before the first forward pass, so untrained values
    # can never produce a response.
    generator, _ = _build_gan(torch, latent_dim, hidden_dim, output_dim)
    try:
        generator.load_state_dict(state_dict, strict=True)
    except Exception as exc:
        raise ExperimentAlgorithmError(f"GAN artifact weights do not match metadata: {exc}") from exc
    generator.eval()
    random = torch.Generator(device="cpu")
    random.manual_seed(seed)
    with torch.no_grad():
        noise = torch.randn((count, latent_dim), generator=random, device="cpu")
        samples = generator(noise).cpu().tolist()
    return {
        "kind": "gan_generation",
        "samples": samples,
        "count": count,
        "feature_count": output_dim,
        "seed": seed,
    }


__all__ = [
    "AlgorithmUnavailableError",
    "ExperimentAlgorithmError",
    "generate_gan_from_artifact",
    "predict_gnn_from_artifact",
    "run_gan",
    "run_gnn",
]
