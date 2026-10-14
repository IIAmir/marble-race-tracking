"""Per-camera colour models used to identify marbles."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


def bgr_to_lab(pixels) -> np.ndarray:
    """Convert an (N, 3) uint8 BGR array to float CIELAB (L in 0..100, a/b ~ -127..127)."""
    px = np.asarray(pixels, dtype=np.float32).reshape(-1, 1, 3) / 255.0
    return cv2.cvtColor(px, cv2.COLOR_BGR2Lab).reshape(-1, 3)


@dataclass
class ColorModel:
    """Gaussian model of one marble's pixel colours in CIELAB, as seen by one camera.

    The covariance absorbs the shading, rim darkening and illumination fall-off
    the marble shows across that camera's field of view. Specular highlights are
    trimmed during fitting so they do not inflate the model.
    """

    mean: np.ndarray
    cov_inv: np.ndarray

    @classmethod
    def fit(cls, lab, reg: float = 4.0, iters: int = 3, keep_d2: float = 11.34) -> "ColorModel":
        lab = np.asarray(lab, dtype=np.float64)
        if len(lab) < 10:
            raise ValueError("not enough calibration pixels")
        x = lab
        for _ in range(iters):
            mean = x.mean(axis=0)
            cov = np.cov(x.T) + reg * np.eye(3)
            model = cls(mean, np.linalg.inv(cov))
            inliers = lab[model.d2(lab) < keep_d2]  # chi2(3 dof) 99%
            if len(inliers) < 10:
                break
            x = inliers
        return model

    def d2(self, lab) -> np.ndarray:
        """Squared Mahalanobis distance of each Lab row to this model."""
        diff = np.asarray(lab, dtype=np.float64).reshape(-1, 3) - self.mean
        return np.einsum("ni,ij,nj->n", diff, self.cov_inv, diff)


def d2_matrix(models: list[ColorModel], lab) -> np.ndarray:
    """(N, K) squared Mahalanobis distances of N Lab pixels to K marble models.

    Each model's distance (x - mu)^T A (x - mu) is a quadratic polynomial in the
    pixel's (L, a, b), so all K models are scored with one (N, 10) x (10, K)
    matrix product instead of K separate per-pixel quadratic forms.
    """
    x = np.asarray(lab, dtype=np.float64).reshape(-1, 3)
    feats = np.column_stack([x[:, 0] ** 2, x[:, 1] ** 2, x[:, 2] ** 2, x[:, 0] * x[:, 1], x[:, 0] * x[:, 2],
                             x[:, 1] * x[:, 2], x[:, 0], x[:, 1], x[:, 2], np.ones(len(x))])
    W = np.empty((10, len(models)))
    for k, m in enumerate(models):
        A, mu = m.cov_inv, m.mean
        b = A @ mu
        W[:, k] = [A[0, 0], A[1, 1], A[2, 2], 2 * A[0, 1], 2 * A[0, 2], 2 * A[1, 2], -2 * b[0], -2 * b[1], -2 * b[2], mu @ b]
    return np.maximum(feats @ W, 0.0)


def palette_separation(models: list[ColorModel]) -> np.ndarray:
    """(K, K) matrix of how distinguishable each pair of marbles is for one camera.

    Entry (i, j) is the smaller of: marble i's mean colour scored against marble
    j's model and vice versa (squared Mahalanobis). Values below ~50 mean the pair
    can be confused under this camera's lighting; the calibration tool flags them
    so the marble set can be changed before race day.
    """
    k = len(models)
    out = np.full((k, k), np.inf)
    for i in range(k):
        for j in range(k):
            if i != j:
                out[i, j] = min(models[j].d2(models[i].mean)[0], models[i].d2(models[j].mean)[0])
    return out
