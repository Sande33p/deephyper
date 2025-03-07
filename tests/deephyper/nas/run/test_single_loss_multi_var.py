import pytest

import numpy as np


def load_data(dim=10):
    """
    Generate data for linear function -sum(x_i).

    Return:
        Tuple of Numpy arrays: ``(train_X, train_y), (valid_X, valid_y)``.
    """
    rs = np.random.RandomState(42)
    size = 100000
    prop = 0.80
    a, b = 0, 100
    d = b - a
    x = np.array([a + rs.random(dim) * d for i in range(size)])
    y = np.array([[np.sum(v), -np.sum(v)] for v in x])

    sep_index = int(prop * size)
    train_X = x[:sep_index]
    train_y = y[:sep_index]

    valid_X = x[sep_index:]
    valid_y = y[sep_index:]

    print(f"train_X shape: {np.shape(train_X)}")
    print(f"train_y shape: {np.shape(train_y)}")
    print(f"valid_X shape: {np.shape(valid_X)}")
    print(f"valid_y shape: {np.shape(valid_y)}")
    return (train_X, train_y), (valid_X, valid_y)


@pytest.mark.nas
def test_single_loss_multi_var():
    from deephyper.nas.run import run_base_trainer
    from deephyper.problem import NaProblem
    from deephyper.nas.spacelib.tabular import OneLayerSpace

    Problem = NaProblem()
    Problem.load_data(load_data)
    Problem.search_space(OneLayerSpace)
    Problem.hyperparameters(
        batch_size=100, learning_rate=0.1, optimizer="adam", num_epochs=1
    )
    Problem.loss("mse")
    Problem.metrics(["r2"])
    Problem.objective("val_r2")

    config = Problem.space
    config["hyperparameters"]["verbose"] = 1

    # Baseline
    config["arch_seq"] = [0.5]

    run_base_trainer(config)


if __name__ == "__main__":
    test_single_loss_multi_var()
