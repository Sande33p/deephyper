from deephyper.search.nas.contrib.anl_mlp_2 import create_structure
from deephyper.search.nas.model.preprocessing import stdscaler
from deephyper.benchmark import Problem
from deephyper.benchmark.nas.saddleReg.load_data import load_data

Problem = Problem()
Problem.add_dim('regression', True)
Problem.add_dim('load_data', {
    'func': load_data
})
Problem.add_dim('preprocessing', {
    'func': stdscaler
})
Problem.add_dim('create_structure', {
    'func': create_structure,
    'kwargs': {
        'num_cells': 5
    }
})
Problem.add_dim('hyperparameters', {
    'batch_size': 100,
    'learning_rate': 0.01,
    'optimizer': 'adam',
    'num_epochs': 50,
    'loss_metric': 'mean_squared_error',
    'test_metric': 'mean_squared_error',
})


if __name__ == '__main__':
    print(Problem)
