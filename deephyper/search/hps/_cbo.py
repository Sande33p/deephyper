import logging
import math
import time
import warnings

import ConfigSpace as CS
import ConfigSpace.hyperparameters as csh
import numpy as np
import pandas as pd
import deephyper.skopt
from deephyper.problem._hyperparameter import convert_to_skopt_space
from deephyper.search._search import Search

from sklearn.ensemble import GradientBoostingRegressor
from deephyper.skopt.utils import use_named_args

# Adapt minimization -> maximization with DeepHyper
MAP_multi_point_strategy = {"cl_min": "cl_max", "cl_max": "cl_min", "qUCB": "qLCB"}

MAP_acq_func = {"UCB": "LCB", "qUCB": "qLCB"}

MAP_filter_failures = {"min": "max"}


class CBO(Search):
    """Centralized Bayesian Optimisation Search, previously named as "Asynchronous Model-Based Search" (AMBS). It follows a manager-workers architecture where the manager runs the Bayesian optimization loop and workers execute parallel evaluations of the black-box function.

    Example Usage:

        >>> search = CBO(problem, evaluator)
        >>> results = search.search(max_evals=100, timeout=120)

    Args:
        problem (HpProblem): Hyperparameter problem describing the search space to explore.
        evaluator (Evaluator): An ``Evaluator`` instance responsible of distributing the tasks.
        random_state (int, optional): Random seed. Defaults to ``None``.
        log_dir (str, optional): Log directory where search's results are saved. Defaults to ``"."``.
        verbose (int, optional): Indicate the verbosity level of the search. Defaults to ``0``.
        surrogate_model (str, optional): Surrogate model used by the Bayesian optimization. Can be a value in ``["RF", "ET", "GBRT", "DUMMY"]``. Defaults to ``"RF"``.
        acq_func (str, optional): Acquisition function used by the Bayesian optimization. Can be a value in ``["UCB", "EI", "PI", "gp_hedge"]``. Defaults to ``"UCB"``.
        acq_optimizer (str, optional): Method used to minimze the acquisition function. Can be a value in ``["sampling", "lbfgs"]``. Defaults to ``"auto"``.
        kappa (float, optional): Manage the exploration/exploitation tradeoff for the "UCB" acquisition function. Defaults to ``1.96`` which corresponds to 95% of the confidence interval.
        xi (float, optional): Manage the exploration/exploitation tradeoff of ``"EI"`` and ``"PI"`` acquisition function. Defaults to ``0.001``.
        n_points (int, optional): The number of configurations sampled from the search space to infer each batch of new evaluated configurations.
        filter_duplicated (bool, optional): Force the optimizer to sample unique points until the search space is "exhausted" in the sens that no new unique points can be found given the sampling size ``n_points``. Defaults to ``True``.
        multi_point_strategy (str, optional): Definition of the constant value use for the Liar strategy. Can be a value in ``["cl_min", "cl_mean", "cl_max", "qUCB"]`` . Defaults to ``"cl_max"``.
        n_jobs (int, optional): Number of parallel processes used to fit the surrogate model of the Bayesian optimization. A value of ``-1`` will use all available cores. Defaults to ``1``.
        n_initial_points (int, optional): Number of collected objectives required before fitting the surrogate-model. Defaults to ``10``.
        initial_points (List[Dict], optional): A list of initial points to evaluate. Defaults to ``None``.
        sync_communcation (bool, optional): Performs the search in a batch-synchronous manner. Defaults to ``False``.
        filter_failures (str, optional): Replace objective of failed configurations by ``"min"`` or ``"mean"``. If ``"ignore"`` is passed then failed configurations will be filtered-out and not passed to the surrogate model. Defaults to ``"mean"`` to replace by failed configurations by the running mean of objectives.
    """

    def __init__(
        self,
        problem,
        evaluator,
        random_state: int = None,
        log_dir: str = ".",
        verbose: int = 0,
        surrogate_model: str = "RF",
        acq_func: str = "UCB",
        acq_optimizer: str = "auto",
        kappa: float = 1.96,
        xi: float = 0.001,
        n_points: int = 10000,
        filter_duplicated: bool = True,
        update_prior: bool = False,
        multi_point_strategy: str = "cl_max",
        n_jobs: int = 1,  # 32 is good for Theta
        n_initial_points=10,
        initial_points=None,
        sync_communication: bool = False,
        filter_failures: str = "mean",
        **kwargs,
    ):

        super().__init__(problem, evaluator, random_state, log_dir, verbose)

        # check input parameters
        surrogate_model_allowed = ["RF", "ET", "GBRT", "DUMMY", "GP"]
        if not (surrogate_model in surrogate_model_allowed):
            raise ValueError(
                f"Parameter 'surrogate_model={surrogate_model}' should have a value in {surrogate_model_allowed}!"
            )

        acq_func_allowed = ["UCB", "EI", "PI", "gp_hedge", "qUCB"]
        if not (acq_func in acq_func_allowed):
            raise ValueError(
                f"Parameter 'acq_func={acq_func}' should have a value in {acq_func_allowed}!"
            )

        if not (np.isscalar(kappa)):
            raise ValueError(f"Parameter 'kappa' should be a scalar value!")

        if not (np.isscalar(xi)):
            raise ValueError("Parameter 'xi' should be a scalar value!")

        if not (type(n_points) is int):
            raise ValueError("Parameter 'n_points' shoud be an integer value!")

        if not (type(filter_duplicated) is bool):
            raise ValueError(
                f"Parameter filter_duplicated={filter_duplicated} should be a boolean value!"
            )

        multi_point_strategy_allowed = [
            "cl_min",
            "cl_mean",
            "cl_max",
            "topk",
            "boltzmann",
            "qUCB",
        ]
        if not (multi_point_strategy in multi_point_strategy_allowed):
            raise ValueError(
                f"Parameter multi_point_strategy={multi_point_strategy} should have a value in {multi_point_strategy_allowed}!"
            )

        if not (type(n_jobs) is int):
            raise ValueError(f"Parameter n_jobs={n_jobs} should be an integer value!")

        self._n_initial_points = n_initial_points
        self._initial_points = []
        if initial_points is not None and len(initial_points) > 0:
            for point in initial_points:
                if isinstance(point, list):
                    self._initial_points.append(point)
                elif isinstance(point, dict):
                    self._initial_points.append([point[hp_name] for hp_name in problem.hyperparameter_names])
                else:
                    raise ValueError(f"Initial points should be dict or list but {type(point)} was given!")


        self._multi_point_strategy = MAP_multi_point_strategy.get(
            multi_point_strategy, multi_point_strategy
        )
        self._fitted = False

        # check if it is possible to convert the ConfigSpace to standard skopt Space
        if (
            isinstance(self._problem.space, CS.ConfigurationSpace)
            and len(self._problem.space.get_forbiddens()) == 0
            and len(self._problem.space.get_conditions()) == 0
        ):
            self._opt_space = convert_to_skopt_space(self._problem.space)
        else:
            self._opt_space = self._problem.space

        self._opt = None
        self._opt_kwargs = dict(
            dimensions=self._opt_space,
            base_estimator=self._get_surrogate_model(
                surrogate_model,
                n_jobs,
                random_state=self._random_state.randint(0, 2**32),
            ),
            # optimizer
            acq_optimizer=acq_optimizer,
            acq_optimizer_kwargs={
                "n_points": n_points,
                "filter_duplicated": filter_duplicated,
                "update_prior": update_prior,
                "n_jobs": n_jobs,
                "filter_failures": MAP_filter_failures.get(
                    filter_failures, filter_failures
                ),
            },
            # acquisition function
            acq_func=MAP_acq_func.get(acq_func, acq_func),
            acq_func_kwargs={"xi": xi, "kappa": kappa},
            n_initial_points=self._n_initial_points,
            initial_points=self._initial_points,
            random_state=self._random_state,
        )

        self._gather_type = "ALL" if sync_communication else "BATCH"

    def _setup_optimizer(self):
        if self._fitted:
            self._opt_kwargs["n_initial_points"] = 0
        self._opt = deephyper.skopt.Optimizer(**self._opt_kwargs)

    def _search(self, max_evals, timeout):

        if self._opt is None:
            self._setup_optimizer()

        num_evals_done = 0

        logging.info(f"Asking {self._evaluator.num_workers} initial configurations...")
        t1 = time.time()
        new_X = self._opt.ask(n_points=self._evaluator.num_workers)
        logging.info(f"Asking took {time.time() - t1:.4f} sec.")

        # Transform list to dict configurations
        logging.info(f"Transforming configurations to dict...")
        t1 = time.time()
        new_batch = []
        for x in new_X:
            new_cfg = self._to_dict(x)
            new_batch.append(new_cfg)
        logging.info(f"Transformation took {time.time() - t1:.4f} sec.")

        # submit new configurations
        logging.info(f"Submitting {len(new_batch)} configurations...")
        t1 = time.time()
        self._evaluator.submit(new_batch)
        logging.info(f"Submition took {time.time() - t1:.4f} sec.")

        # Main loop
        while max_evals < 0 or num_evals_done < max_evals:
            # Collecting finished evaluations
            logging.info("Gathering jobs...")
            t1 = time.time()
            new_results = self._evaluator.gather(self._gather_type, size=1)
            logging.info(
                f"Gathered {len(new_results)} job(s) in {time.time() - t1:.4f} sec."
            )

            if len(new_results) > 0:

                logging.info("Dumping evaluations...")
                t1 = time.time()
                self._evaluator.dump_evals(log_dir=self._log_dir)
                logging.info(f"Dumping took {time.time() - t1:.4f} sec.")

                num_received = len(new_results)
                num_evals_done += num_received

                if max_evals > 0 and num_evals_done >= max_evals:
                    break

                # Transform configurations to list to fit optimizer
                logging.info("Transforming received configurations to list...")
                t1 = time.time()

                opt_X = []
                opt_y = []
                for cfg, obj in new_results:
                    x = list(cfg.values())
                    if np.isreal(obj):
                        opt_X.append(x)
                        opt_y.append(-obj)  #! maximizing
                    elif type(obj) is str and "F" == obj[0]:
                        if (
                            self._opt_kwargs["acq_optimizer_kwargs"]["filter_failures"]
                            == "ignore"
                        ):
                            continue
                        else:
                            opt_X.append(x)
                            opt_y.append("F")

                logging.info(f"Transformation took {time.time() - t1:.4f} sec.")

                logging.info("Fitting the optimizer...")
                t1 = time.time()

                if len(opt_y) > 0:
                    self._opt.tell(opt_X, opt_y)
                    logging.info(f"Fitting took {time.time() - t1:.4f} sec.")

                logging.info(f"Asking {len(new_results)} new configurations...")
                t1 = time.time()
                new_X = self._opt.ask(
                    n_points=len(new_results), strategy=self._multi_point_strategy
                )
                logging.info(f"Asking took {time.time() - t1:.4f} sec.")

                # Transform list to dict configurations
                logging.info(f"Transforming configurations to dict...")
                t1 = time.time()
                new_batch = []
                for x in new_X:
                    new_cfg = self._to_dict(x)
                    new_batch.append(new_cfg)
                logging.info(f"Transformation took {time.time() - t1:.4f} sec.")

                # submit new configurations
                logging.info(f"Submitting {len(new_batch)} configurations...")
                t1 = time.time()
                self._evaluator.submit(new_batch)
                logging.info(f"Submition took {time.time() - t1:.4f} sec.")

    def _get_surrogate_model(
        self, name: str, n_jobs: int = None, random_state: int = None
    ):
        """Get a surrogate model from Scikit-Optimize.

        Args:
            name (str): name of the surrogate model.
            n_jobs (int): number of parallel processes to distribute the computation of the surrogate model.

        Raises:
            ValueError: when the name of the surrogate model is unknown.
        """
        accepted_names = ["RF", "ET", "GBRT", "DUMMY", "GP"]
        if not (name in accepted_names):
            raise ValueError(
                f"Unknown surrogate model {name}, please choose among {accepted_names}."
            )

        if name == "RF":
            surrogate = deephyper.skopt.learning.RandomForestRegressor(
                n_estimators=100,
                max_features=1,
                # min_samples_leaf=3,
                n_jobs=n_jobs,
                random_state=random_state,
            )
        elif name == "ET":
            surrogate = deephyper.skopt.learning.ExtraTreesRegressor(
                n_estimators=100,
                min_samples_leaf=3,
                n_jobs=n_jobs,
                random_state=random_state,
            )
        elif name == "GBRT":

            gbrt = GradientBoostingRegressor(n_estimators=30, loss="quantile")
            surrogate = deephyper.skopt.learning.GradientBoostingQuantileRegressor(
                base_estimator=gbrt, n_jobs=n_jobs, random_state=random_state
            )
        else:  # for DUMMY and GP
            surrogate = name

        return surrogate

    def _return_cond(self, cond, cst_new):
        parent = cst_new.get_hyperparameter(cond.parent.name)
        child = cst_new.get_hyperparameter(cond.child.name)
        if type(cond) == CS.EqualsCondition:
            value = cond.value
            cond_new = CS.EqualsCondition(child, parent, cond.value)
        elif type(cond) == CS.GreaterThanCondition:
            value = cond.value
            cond_new = CS.GreaterThanCondition(child, parent, value)
        elif type(cond) == CS.NotEqualsCondition:
            value = cond.value
            cond_new = CS.GreaterThanCondition(child, parent, value)
        elif type(cond) == CS.LessThanCondition:
            value = cond.value
            cond_new = CS.GreaterThanCondition(child, parent, value)
        elif type(cond) == CS.InCondition:
            values = cond.values
            cond_new = CS.GreaterThanCondition(child, parent, values)
        else:
            print("Not supported type" + str(type(cond)))
        return cond_new

    def _return_forbid(self, cond, cst_new):
        if type(cond) == CS.ForbiddenEqualsClause or type(cond) == CS.ForbiddenInClause:
            hp = cst_new.get_hyperparameter(cond.hyperparameter.name)
            if type(cond) == CS.ForbiddenEqualsClause:
                value = cond.value
                cond_new = CS.ForbiddenEqualsClause(hp, value)
            elif type(cond) == CS.ForbiddenInClause:
                values = cond.values
                cond_new = CS.ForbiddenInClause(hp, values)
            else:
                print("Not supported type" + str(type(cond)))
        return cond_new

    def fit_surrogate(self, df):
        """Fit the surrogate model of the search from a checkpointed Dataframe.

        Args:
            df (str|DataFrame): a checkpoint from a previous search.

        Example Usage:

        >>> search = CBO(problem, evaluator)
        >>> search.fit_surrogate("results.csv")
        """
        if type(df) is str and df[-4:] == ".csv":
            df = pd.read_csv(df)
        assert isinstance(df, pd.DataFrame)

        self._fitted = True

        if self._opt is None:
            self._setup_optimizer()

        hp_names = self._problem.hyperparameter_names
        try:
            x = df[hp_names].values.tolist()
            y = df.objective.tolist()
        except KeyError:
            raise ValueError(
                "Incompatible dataframe 'df' to fit surrogate model of CBO."
            )

        self._opt.tell(x, [-yi for yi in y])

    def fit_generative_model(self, df, q=0.90, n_iter_optimize=0, n_samples=100):
        """Learn the distribution of hyperparameters for the top-``(1-q)x100%`` configurations and sample from this distribution. It can be used for transfer learning.

        Example Usage:

        >>> search = CBO(problem, evaluator)
        >>> search.fit_surrogate("results.csv")

        Args:
            df (str|DataFrame): a dataframe or path to CSV from a previous search.
            q (float, optional): the quantile defined the set of top configurations used to bias the search. Defaults to ``0.90`` which select the top-10% configurations from ``df``.
            n_iter_optimize (int, optional): the number of iterations used to optimize the generative model which samples the data for the search. Defaults to ``0`` with no optimization for the generative model.
            n_samples (int, optional): the number of samples used to score the generative model.

        Returns:
            tuple: ``score, model`` which are a metric which measures the quality of the learned generated-model and the generative model respectively.
        """
        # to make sdv optional
        try:
            import sdv
        except ImportError as e:
            print("Install SDV with: pip install sdv")
            raise e

        if type(df) is str and df[-4:] == ".csv":
            df = pd.read_csv(df)
        assert isinstance(df, pd.DataFrame)

        # filter failures
        df = df[~df.objective.str.startswith("F")]
        df.objective = df.objective.astype(float)

        # print(df.objective.values)
        q_val = np.quantile(df.objective.values, q)
        req_df = df.loc[df["objective"] > q_val]
        req_df = req_df.drop(
            columns=["job_id", "objective", "timestamp_submit", "timestamp_gather"]
        )

        # constraints
        scalar_constraints = []
        for hp_name in self._problem.space:
            if hp_name in req_df.columns:
                hp = self._problem.space.get_hyperparameter(hp_name)

                # TODO: Categorical and Ordinal are both considered non-ordered for SDV
                # TODO: it could be useful to use the "category"  type of Pandas and the ordered=True/False argument
                # TODO: to extend the capability of SDV
                if isinstance(hp, csh.CategoricalHyperparameter) or isinstance(
                    hp, csh.OrdinalHyperparameter
                ):
                    req_df[hp_name] = req_df[hp_name].astype("O")
                else:
                    scalar_constraints.append(
                        sdv.constraints.Between(hp_name, hp.lower, hp.upper)
                    )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            model = sdv.tabular.TVAE(constraints=scalar_constraints)
            model.fit(req_df)
            synthetic_data = model.sample(n_samples)
            score = sdv.evaluation.evaluate(synthetic_data, req_df)

            if n_iter_optimize > 0:
                space = [
                    deephyper.skopt.space.Integer(1, 20, name="epochs"),
                    # deephyper.skopt.space.Integer(1, np.floor(req_df.shape[0]/10), name='batch_size'),
                    deephyper.skopt.space.Integer(1, 8, name="embedding_dim"),
                    deephyper.skopt.space.Integer(1, 8, name="compress_dims"),
                    deephyper.skopt.space.Integer(1, 8, name="decompress_dims"),
                    deephyper.skopt.space.Real(
                        10**-8, 10**-4, "log-uniform", name="l2scale"
                    ),
                    deephyper.skopt.space.Integer(1, 5, name="loss_factor"),
                ]

                def model_fit(params):
                    params["epochs"] = 10 * params["epochs"]
                    # params['batch_size'] = 10*params['batch_size']
                    params["embedding_dim"] = 2 ** params["embedding_dim"]
                    params["compress_dims"] = [
                        2 ** params["compress_dims"],
                        2 ** params["compress_dims"],
                    ]
                    params["decompress_dims"] = [
                        2 ** params["decompress_dims"],
                        2 ** params["decompress_dims"],
                    ]
                    model = sdv.tabular.TVAE(**params)
                    model.fit(req_df)
                    synthetic_data = model.sample(n_samples)
                    score = sdv.evaluation.evaluate(synthetic_data, req_df)
                    return -score, model

                @use_named_args(space)
                def objective(**params):
                    score, _ = model_fit(params)
                    return score

                # run sequential optimization of generative model hyperparameters
                opt = deephyper.skopt.Optimizer(space)
                for i in range(n_iter_optimize):
                    x = opt.ask()
                    y = objective(x)
                    opt.tell(x, y)
                    logging.info(f"iteration {i}: {x} -> {y}")

                min_index = np.argmin(opt.yi)
                best_params = opt.Xi[min_index]
                logging.info(
                    f"Min-Score of the SDV generative model: {opt.yi[min_index]}"
                )

                best_params = {d.name: v for d, v in zip(space, best_params)}
                logging.info(
                    f"Best configuration for SDV generative model: {best_params}"
                )

                score, model = model_fit(best_params)

        # we pass the learned generative model from sdv to the
        # skopt Optimizer
        self._opt_kwargs["model_sdv"] = model

        return score, model

    def fit_search_space(self, df, fac_numerical=0.125, fac_categorical=10):
        """Apply prior-guided transfer learning based on a DataFrame of results.

        Example Usage:

        >>> search = CBO(problem, evaluator)
        >>> search.fit_surrogate("results.csv")

        Args:
            df (str|DataFrame): a checkpoint from a previous search.
            fac_numerical (float): the factor used to compute the sigma of a truncated normal distribution based on ``sigma = max(1.0, (upper - lower) * fac_numerical)``. A small large factor increase exploration while a small factor increase exploitation around the best-configuration from the ``df`` parameter.
            fac_categorical (float): the weight given to a categorical feature part of the best configuration. A large weight ``> 1`` increase exploitation while a small factor close to ``1`` increase exploration.
        """

        if type(df) is str and df[-4:] == ".csv":
            df = pd.read_csv(df)
        assert isinstance(df, pd.DataFrame)

        # filter failures
        df = df[~df.objective.str.startswith("F")]
        df.objective = df.objective.astype(float)

        cst = self._problem.space
        if type(cst) != CS.ConfigurationSpace:
            logging.error(f"{type(cst)}: not supported for trainsfer learning")

        res_df = df
        res_df_names = res_df.columns.values
        best_index = np.argmax(res_df["objective"].values)
        best_param = res_df.iloc[best_index]

        cst_new = CS.ConfigurationSpace(seed=self._random_state.randint(0, 2**32))
        hp_names = cst.get_hyperparameter_names()
        for hp_name in hp_names:
            hp = cst.get_hyperparameter(hp_name)
            if hp_name in res_df_names:
                if (
                    type(hp) is csh.UniformIntegerHyperparameter
                    or type(hp) is csh.UniformFloatHyperparameter
                ):
                    mu = best_param[hp.name]
                    lower = hp.lower
                    upper = hp.upper
                    sigma = max(1.0, (upper - lower) * fac_numerical)
                    if type(hp) is csh.UniformIntegerHyperparameter:
                        param_new = csh.NormalIntegerHyperparameter(
                            name=hp.name,
                            default_value=mu,
                            mu=mu,
                            sigma=sigma,
                            lower=lower,
                            upper=upper,
                        )
                    else:  # type is csh.UniformFloatHyperparameter:
                        param_new = csh.NormalFloatHyperparameter(
                            name=hp.name,
                            default_value=mu,
                            mu=mu,
                            sigma=sigma,
                            lower=lower,
                            upper=upper,
                        )
                    cst_new.add_hyperparameter(param_new)
                elif (
                    type(hp) is csh.CategoricalHyperparameter
                    or type(hp) is csh.OrdinalHyperparameter
                ):
                    if type(hp) is csh.OrdinalHyperparameter:
                        choices = hp.sequence
                    else:
                        choices = hp.choices
                    weights = len(choices) * [1.0]
                    index = choices.index(best_param[hp.name])
                    weights[index] = fac_categorical
                    norm_weights = [float(i) / sum(weights) for i in weights]
                    param_new = csh.CategoricalHyperparameter(
                        name=hp.name, choices=choices, weights=norm_weights
                    )
                    cst_new.add_hyperparameter(param_new)
                else:
                    logging.warning(f"Not fitting {hp} because it is not supported!")
                    cst_new.add_hyperparameter(hp)
            else:
                logging.warning(
                    f"Not fitting {hp} because it was not found in the dataframe!"
                )
                cst_new.add_hyperparameter(hp)

        # For conditions
        for cond in cst.get_conditions():
            if type(cond) == CS.AndConjunction or type(cond) == CS.OrConjunction:
                cond_list = []
                for comp in cond.components:
                    cond_list.append(self._return_cond(comp, cst_new))
                if type(cond) is CS.AndConjunction:
                    cond_new = CS.AndConjunction(*cond_list)
                elif type(cond) is CS.OrConjunction:
                    cond_new = CS.OrConjunction(*cond_list)
                else:
                    logging.warning(f"Condition {type(cond)} is not implemented!")
            else:
                cond_new = self._return_cond(cond, cst_new)
            cst_new.add_condition(cond_new)

        # For forbiddens
        for cond in cst.get_forbiddens():
            if type(cond) is CS.ForbiddenAndConjunction:
                cond_list = []
                for comp in cond.components:
                    cond_list.append(self._return_forbid(comp, cst_new))
                cond_new = CS.ForbiddenAndConjunction(*cond_list)
            elif (
                type(cond) is CS.ForbiddenEqualsClause
                or type(cond) is CS.ForbiddenInClause
            ):
                cond_new = self._return_forbid(cond, cst_new)
            else:
                logging.warning(f"Forbidden {type(cond)} is not implemented!")
            cst_new.add_forbidden_clause(cond_new)

        self._opt_kwargs["dimensions"] = cst_new

    def _to_dict(self, x: list) -> dict:
        """Transform a list of hyperparameter values to a ``dict`` where keys are hyperparameters names and values are hyperparameters values.

        Args:
            x (list): a list of hyperparameter values.

        Returns:
            dict: a dictionnary of hyperparameter names and values.
        """
        res = {}
        hps_names = self._problem.hyperparameter_names
        for i in range(len(x)):
            res[hps_names[i]] = x[i]
        return res


class AMBS(CBO):
    """AMBS is now deprecated and will be removed in the future use 'CBO' (Centralized Bayesian Optimization) instead! """

    def __init__(
        self,
        problem,
        evaluator,
        random_state: int = None,
        log_dir: str = ".",
        verbose: int = 0,
        surrogate_model: str = "RF",
        acq_func: str = "UCB",
        acq_optimizer: str = "auto",
        kappa: float = 1.96,
        xi: float = 0.001,
        n_points: int = 10000,
        filter_duplicated: bool = True,
        update_prior: bool = False,
        multi_point_strategy: str = "cl_max",
        n_jobs: int = 1,
        n_initial_points=10,
        initial_points=None,
        sync_communication: bool = False,
        filter_failures: str = "mean",
        **kwargs,
    ):
        super().__init__(
            problem,
            evaluator,
            random_state,
            log_dir,
            verbose,
            surrogate_model,
            acq_func,
            acq_optimizer,
            kappa,
            xi,
            n_points,
            filter_duplicated,
            update_prior,
            multi_point_strategy,
            n_jobs,
            n_initial_points,
            initial_points,
            sync_communication,
            filter_failures,
            **kwargs,
        )
        import warnings

        warnings.warn(
            "'AMBS' is now deprecated and will be removed in the future use 'CBO' (Centralized Bayesian Optimization) instead!",
            category=DeprecationWarning,
        )
