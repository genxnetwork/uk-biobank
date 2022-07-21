import json
import numpy
from omegaconf import DictConfig
from time import time
from pytorch_lightning import Callback
import torch
from logging import Logger
import mlflow
from typing import Dict, List, OrderedDict, Tuple, Any
from pytorch_lightning.trainer import Trainer
from pytorch_lightning.utilities.model_summary import _format_summary_table, summarize
from abc import ABC, abstractmethod

from flwr.client import NumPyClient
from flwr.common import Weights, parameters_to_weights

from nn.models import BaseNet, LinearRegressor, MLPRegressor, LassoNetRegressor
from nn.lightning import DataModule
from nn.utils import Metrics
from fl.federation.callbacks import ScaffoldCallback
from fl.federation.utils import weights_to_module_params, bytes_to_weights


class ModelFactory:
    """Class for creating models based on model name from config

    Raises:
        ValueError: If model is not one of the linear_regressor, mlp_regressor, lassonet_regressor

    """    
    @staticmethod
    def _create_linear_regressor(input_size: int, covariate_count: int, params: Any) -> LinearRegressor:
        return LinearRegressor(
            input_size=input_size,
            l1=params.model.l1,
            optim_params=params['optimizer'],
            scheduler_params=params['scheduler']
        )

    @staticmethod
    def _create_mlp_regressor(input_size: int, covariate_count: int, params: Any) -> MLPRegressor:
        return MLPRegressor(
            input_size=input_size,
            hidden_size=params.model.hidden_size,
            l1=params.model.l1,
            optim_params=params['optimizer'],
            scheduler_params=params['scheduler']
        )

    @staticmethod
    def _create_lassonet_regressor(input_size: int, covariate_count: int, params: Any) -> LassoNetRegressor:
        return LassoNetRegressor(
            input_size=input_size,
            hidden_size=params.model.hidden_size,
            optim_params=params.optimizer,
            scheduler_params=params.scheduler,
            cov_count=covariate_count, 
            alpha_start=params.model.alpha_start,
            alpha_end=params.model.alpha_end,
            init_limit=params.model.init_limit
        )

    @staticmethod
    def create_model(input_size: int, covariate_count: int, params: Any) -> BaseNet:
        model_dict = {
            'linear_regressor': ModelFactory._create_linear_regressor,
            'mlp_regressor': ModelFactory._create_mlp_regressor,
            'lassonet_regressor': ModelFactory._create_lassonet_regressor
        }

        create_func = model_dict.get(params.model.name, None)
        if create_func is None:
            raise ValueError(f'model name {params.model.name} is unknown, it should be one of the {list(model_dict.keys())}')
        return create_func(input_size, covariate_count, params)


class CallbackFactory:
    @staticmethod
    def create_callbacks(params: DictConfig) -> List[Callback]:
        if params.strategy.name == 'scaffold':
            return [ScaffoldCallback(params.strategy.args.K, log_grad=True, log_diff=True)]


class MetricsLogger(ABC):
    @abstractmethod
    def log_eval_metric(self, metric: Metrics):
        pass
    
    @abstractmethod
    def log_weights(self, rnd: int, layers: List[str], old_weights: Weights, new_weights: Weights):
        pass


class MLFlowMetricsLogger(MetricsLogger):
    def log_eval_metric(self, metric: Metrics):
        metric.log_to_mlflow()

    def log_weights(self, rnd: int, layers: List[str], old_weights: Weights, new_weights: Weights):
        # logging.info(f'weights shape: {[w.shape for w in new_weights]}')
        
        client_diffs = {layer: numpy.linalg.norm(cw - aw).item() for layer, cw, aw in zip(layers, old_weights, new_weights)}
        for layer, diff in client_diffs.items():
            mlflow.log_metric(f'{layer}.l2', diff, rnd)


class FLClient(NumPyClient):
    def __init__(self, server: str, data_module: DataModule, params: DictConfig, logger: Logger, metrics_logger: MetricsLogger):
        """Trains {model} in federated setting

        Args:
            server (str): Server address with port
            data_module (DataModule): Module with train, val and test dataloaders
            params (DictConfig): OmegaConf DictConfig with subnodes strategy and node. `node` should have `model`, and `training` parameters
            logger (Logger): Process-specific logger of text messages
            metrics_logger (MetricsLogger): Process-specific logger of metrics and weights (mlflow, neptune, etc)
        """        
        self.server = server
        self.model = ModelFactory.create_model(data_module.feature_count(), data_module.covariate_count(), params.node)
        self.callbacks = CallbackFactory.create_callbacks(params)
        self.data_module = data_module
        self.best_model_path = None
        self.node_params = params.node
        self.logger = logger
        self.metrics_logger = metrics_logger
        self.log(f'cuda device count: {torch.cuda.device_count()}')
        self.log(f'training params are: {self.node_params.training}')


    def log(self, msg):
        self.logger.info(msg)

    def get_parameters(self) -> Weights:
        return [val.cpu().numpy() for _, val in self.model.state_dict().items()]

    def set_parameters(self, weights: Weights):
        state_dict = weights_to_module_params(self._get_layer_names(), weights)
        self.model.load_state_dict(state_dict, strict=True)

    def _get_layer_names(self) -> List[str]:
        return list(self.model.state_dict().keys())

    def update_callbacks(self, update_params: Dict, **kwargs):
        if self.callbacks is None:
            return
        for callback in self.callbacks:
            if isinstance(callback, ScaffoldCallback):
                callback.c_global = weights_to_module_params(self._get_layer_names(), bytes_to_weights(update_params['c_global']))
                callback.update_c_local(
                    kwargs['eta'], callback.c_global, 
                    new_params=self.model.state_dict(),
                    old_params=weights_to_module_params(self._get_layer_names(), kwargs['old_params'])
                )
    
    def _reseed_torch(self):
        torch.manual_seed(self.node_params.index*100 + self.model.current_round)


    def fit(self, parameters: Weights, config):
        # self.log(f'started fitting with config {config}')
        self.update_callbacks(config, eta=self.model.get_current_lr(), old_params=parameters)

        try:
            # to catch spurious error "weakly-referenced object no longer exists"
            # probably ref to some model parameter tensor get lost
            self.set_parameters(parameters)
        except ReferenceError as re:
            self.logger.error(re)
            # we recreate a model and set parameters again
            self.model = ModelFactory.create_model(self.data_module.feature_count(), self.data_module.covariate_count(), self.node_params)
            self.set_parameters(parameters)
        
        start = time()    
        # self.log('fit after set parameters')            
        self.model.train()
        # self.log('set model to train')
        self.model.current_round = config['current_round']
        # because train_dataloader will get the same seed and return the same permutation of training samples each federated round
        self._reseed_torch()
        trainer = Trainer(logger=False, **{**self.node_params.training, **{'callbacks': self.callbacks}})
        
        # self.log('trainer created')
        trainer.fit(self.model, datamodule=self.data_module)
        # self.log('model fitted by trainer')
        end = time()
        self.log(f'node: {self.node_params.index}\tfit elapsed: {end-start:.2f}s')
        return self.get_parameters(), self.data_module.train_len(), {}

    def evaluate(self, parameters: Weights, config):
        self.log(f'starting to set parameters in evaluate with config {config}')
        old_weights = self.get_parameters()
        self.metrics_logger.log_weights(self.model.fl_current_epoch(), self._get_layer_names(), old_weights, parameters)
        self.set_parameters(parameters)
        self.log('set parameters in evaluate')
        self.model.eval()

        start = time()                
        need_test_eval = 'current_round' in config and config['current_round'] == -1
        self.log(f'starting predict and eval with {need_test_eval}')
        unreduced_metrics = self.model.predict_and_eval(self.data_module, 
                                                        test=need_test_eval)

        self.metrics_logger.log_eval_metric(unreduced_metrics)
        val_len = self.data_module.val_len()
        end = time()
        self.log(f'node: {self.node_params.index}\tround: {self.model.current_round}\t' + str(unreduced_metrics) + f'\telapsed: {end-start:.2f}s')
        # print(f'round: {self.model.current_round}\t' + str(unreduced_metrics))
        
        results = unreduced_metrics.to_result_dict()
        return unreduced_metrics.val_loss, val_len, results
