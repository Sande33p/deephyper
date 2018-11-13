from deephyper.benchmarks import Problem
from deephyper.benchmarks.nas.mnist1D.load_data import load_data
from deephyper.searches.nas.contrib.anl_mlp_2_keras import create_structure

# We create our Problem object with the Problem class, you don't have to name your Problem object 'Problem' it can be any name you want. You can also define different problems in the same module.
Problem = Problem()

# You define if your problem is a regression problem (the reward will be minus of the mean squared error) or a classification problem (the reward will be the accuracy of the network on the validation set).
Problem.add_dim('regression', False)

# You define how to load your data by giving a 'load_data' function. This function will return your data set following this interface: (train_X, train_y), (valid_X, valid_y). You can also add a 'kwargs' key with arguments for the load_data function.
Problem.add_dim('load_data', {
    'func': load_data,
})

# You define the create structure function. This function will return an object following the Structure interface. You can also have kwargs arguments such as 'num_cells' for this function.
Problem.add_dim('create_structure', {
    'func': create_structure,
    'kwargs': {
        'num_cells': 5
    }
})

# You define the hyperparameters used to train your generated models during the search.
Problem.add_dim('hyperparameters', {
    'batch_size': 100,
    'learning_rate': 0.01,
    'optimizer': 'adam',
    'num_epochs': 20,
    'loss_metric': 'categorical_crossentropy',
    'metrics': ['acc']
})

# Just to print your problem, to test its definition and imports in the current python environment.
if __name__ == '__main__':
    print(Problem)
