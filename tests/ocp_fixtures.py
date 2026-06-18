"""Toy LTI plants for OCP unit tests."""

from __future__ import annotations

import numpy as np

from custom_components.heating_assistant.mbc.models import (
    LinearDiscreteModel,
    LinearContinuousDiscreteModel,
)


class FirstOrderDiscreteModel(LinearDiscreteModel):
    """
    Scalar first-order discrete heating plant.

        x[k+1] = a x[k] + b u[k] + c d[k]
        z[k]   = x[k]
    """

    def __init__(
        self,
        *,
        a: float = 0.92,
        b: float = 0.08,
        c: float = 0.05,
        x0: float = 18.0,
        x_ref: float = 22.0,
        u_min: float = 0.0,
        u_max: float = 1.0,
    ) -> None:
        self._a = float(a)
        self._b = float(b)
        self._c = float(c)
        self._x = np.array([x0], dtype=float)
        self._x_ref = np.array([x_ref], dtype=float)
        self._u_min = np.array([u_min], dtype=float)
        self._u_max = np.array([u_max], dtype=float)

    @property
    def nx(self) -> int:
        return 1

    @property
    def nu(self) -> int:
        return 1

    @property
    def nd(self) -> int:
        return 1

    @property
    def Ad(self) -> np.ndarray:
        return np.array([[self._a]])

    @property
    def Bd(self) -> np.ndarray:
        return np.array([[self._b]])

    @property
    def Ed(self) -> np.ndarray:
        return np.array([[self._c]])

    @property
    def Cm(self) -> np.ndarray:
        return np.array([[1.0]])

    @property
    def Qd(self) -> np.ndarray:
        return np.eye(1) * 1e-6

    @property
    def Rm(self) -> np.ndarray:
        return np.eye(1) * 1e-2

    @property
    def x(self) -> list[float]:
        return self._x.tolist()

    @x.setter
    def x(self, val: list[float]) -> None:
        self._x = np.asarray(val, dtype=float).reshape(1)

    @property
    def x_ref(self) -> np.ndarray:
        return self._x_ref.copy()

    @property
    def u_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self._u_min.copy(), self._u_max.copy()


class FirstOrderCDModel(LinearContinuousDiscreteModel):
    """
    Scalar first-order continuous thermal plant.

        dx/dt = −k (x − d) + b u
        z     = x
    """

    def __init__(
        self,
        *,
        k: float = 0.02,
        b: float = 0.03,
        x0: float = 18.0,
        x_ref: float = 22.0,
        dt: float = 900.0,
        u_min: float = 0.0,
        u_max: float = 1.0,
    ) -> None:
        self._k = float(k)
        self._b = float(b)
        self._dt = float(dt)
        self._x = np.array([x0], dtype=float)
        self._x_ref = np.array([x_ref], dtype=float)
        self._u_min = np.array([u_min], dtype=float)
        self._u_max = np.array([u_max], dtype=float)

    @property
    def nx(self) -> int:
        return 1

    @property
    def nu(self) -> int:
        return 1

    @property
    def nd(self) -> int:
        return 1

    @property
    def A(self) -> np.ndarray:
        return np.array([[-self._k]])

    @property
    def B(self) -> np.ndarray:
        return np.array([[self._b]])

    @property
    def E(self) -> np.ndarray:
        return np.array([[self._k]])

    @property
    def G(self) -> np.ndarray:
        return np.eye(1) * 1e-4

    @property
    def Cm(self) -> np.ndarray:
        return np.array([[1.0]])

    @property
    def Rm(self) -> np.ndarray:
        return np.eye(1) * 1e-2

    @property
    def dt(self) -> float:
        return self._dt

    @property
    def x(self) -> list[float]:
        return self._x.tolist()

    @x.setter
    def x(self, val: list[float]) -> None:
        self._x = np.asarray(val, dtype=float).reshape(1)

    @property
    def x_ref(self) -> np.ndarray:
        return self._x_ref.copy()

    @property
    def u_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self._u_min.copy(), self._u_max.copy()
