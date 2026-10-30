"""Evaluation Metrics Package."""

from eval.kg.metrics.diversity import compute_average_length, compute_vocabulary_entropy

__all__ = ["compute_vocabulary_entropy", "compute_average_length"]
