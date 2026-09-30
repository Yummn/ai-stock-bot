from __future__ import annotations

import os

import numpy as np


class TemporalCNN:
    """A small sequence model; each input contains only bars through its signal date."""

    def __init__(self, epochs: int, seed: int = 42, device: str | None = None, classifier: bool = False):
        import torch

        self.torch = torch
        self.epochs = epochs
        self.seed = seed
        self.classifier = classifier
        preferred = device or os.getenv("STOCKLAB_CNN_DEVICE", "auto")
        self.device = "cuda" if preferred == "auto" and torch.cuda.is_available() else preferred
        if self.device == "auto":
            self.device = "cpu"
        if self.device not in {"cpu", "cuda"}:
            raise ValueError("STOCKLAB_CNN_DEVICE must be auto, cpu, or cuda")
        torch.set_num_threads(2)

    def fit(self, x: np.ndarray, y: np.ndarray):
        torch = self.torch
        from torch import nn

        torch.manual_seed(self.seed)
        if self.device == "cuda":
            torch.cuda.manual_seed_all(self.seed)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
        self.mean = x.mean(axis=(0, 1), dtype=np.float64).astype(np.float32)
        self.std = np.maximum(x.std(axis=(0, 1), dtype=np.float64).astype(np.float32), 1e-5)
        inputs = torch.from_numpy(self._normalize(x)).to(self.device)
        targets = torch.from_numpy(y.astype(np.float32) * (1 if self.classifier else 100)).to(self.device)

        class Network(nn.Module):
            def __init__(self, features: int):
                super().__init__()
                self.convolution = nn.Sequential(
                    nn.Conv1d(features, 16, 3, padding=1), nn.ReLU(),
                    nn.Conv1d(16, 16, 3, padding=1), nn.ReLU(),
                )
                self.head = nn.Sequential(nn.Linear(32, 16), nn.ReLU(), nn.Linear(16, 1))

            def forward(self, batch):
                encoded = self.convolution(batch.transpose(1, 2))
                return self.head(torch.cat((encoded[:, :, -1], encoded.mean(dim=-1)), dim=1)).squeeze(-1)

        self.network = Network(x.shape[2]).to(self.device)
        optimizer = torch.optim.AdamW(self.network.parameters(), lr=.001, weight_decay=.01)
        loss_fn = nn.BCEWithLogitsLoss() if self.classifier else nn.SmoothL1Loss(beta=1.)
        generator = torch.Generator().manual_seed(self.seed)
        for _ in range(self.epochs):
            self.network.train()
            for indices in torch.randperm(len(x), generator=generator).split(512):
                optimizer.zero_grad(set_to_none=True)
                loss = loss_fn(self.network(inputs[indices]), targets[indices])
                if not torch.isfinite(loss):
                    raise ValueError("CNN training produced a non-finite loss")
                loss.backward()
                optimizer.step()
        return self

    def _normalize(self, x):
        return np.clip((x.astype(np.float32) - self.mean) / self.std, -10., 10.)

    def predict(self, x: np.ndarray) -> np.ndarray:
        torch = self.torch
        self.network.eval()
        outputs = []
        with torch.inference_mode():
            for start in range(0, len(x), 1024):
                batch = torch.from_numpy(self._normalize(x[start:start + 1024])).to(self.device)
                values = self.network(batch)
                outputs.append((torch.sigmoid(values) if self.classifier else values / 100.).cpu().numpy())
        return np.concatenate(outputs) if outputs else np.empty(0)


def fit_cnn(x, y, epochs, classifier=False):
    model = TemporalCNN(epochs, classifier=classifier)
    try:
        return model.fit(x, y), None
    except RuntimeError as exc:
        if model.device != "cuda":
            raise
        return TemporalCNN(epochs, device="cpu", classifier=classifier).fit(x, y), type(exc).__name__
