from dataclasses import dataclass
import mlflow
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from flwr.common import weights_to_parameters
import numpy
import pickle


class LoaderMetrics(ABC):
    @abstractmethod
    def log_to_mlflow(self) -> None:
        pass

    @abstractmethod
    def to_result_dict(self) -> Dict:
        pass

class Metrics(LoaderMetrics):
    @property
    @abstractmethod
    def val_loss(self) -> float:
        pass

    def reduce(self, reduction='mean'):
        if reduction not in ['mean']:
            raise ValueError(f'for now, only mean reduction is supported')
        return self


@dataclass
class RegLoaderMetrics(LoaderMetrics):
    # type of dataset, one of the {train, val, test}
    prefix: str
    # loss value
    loss: float
    # r2 value
    r2: float
    # epoch number
    epoch: int
    # count of samples
    samples: int

    def log_to_mlflow(self) -> None:
        mlflow.log_metric(f'{self.prefix}_loss', self.loss, self.epoch)
        mlflow.log_metric(f'{self.prefix}_r2', self.r2, self.epoch)

    def to_result_dict(self) -> Dict:
        return {f'{self.prefix}_loss': self.loss, f'{self.prefix}_r2': self.r2}


@dataclass
class RegMetrics(Metrics):
    train: RegLoaderMetrics
    val: RegLoaderMetrics
    test: RegLoaderMetrics
    epoch: int

    @property
    def val_loss(self) -> float:
        return self.val.loss

    def log_to_mlflow(self) -> None:
        self.train.log_to_mlflow()
        self.val.log_to_mlflow()
        self.test.log_to_mlflow()

    def to_result_dict(self) -> Dict:
        # train_dict, val_dict, test_dict = [m.to_result_dict() for m in [self.train, self.val, self.test]]
        # return train_dict | val_dict | test_dict
        return {'metrics' : pickle.dumps(self)}


@dataclass
class LassoNetRegMetrics(Metrics):
    train: List[RegLoaderMetrics]
    val: List[RegLoaderMetrics]
    test: List[RegLoaderMetrics] = None
    epoch: int = 0
    best_col: int = None

    @property
    def val_loss(self) -> float:
        return self._calculate_mean_metrics(self.val).loss

    def _calculate_mean_metrics(self, metric_list: Optional[List[RegLoaderMetrics]]) -> RegLoaderMetrics:
        if metric_list is None or len(metric_list) == 0:
            return None
        mean_loss = sum([m.loss for m in metric_list])/len(metric_list)
        mean_r2 = sum([m.r2 for m in metric_list])/len(metric_list)
        samples = sum([m.samples for m in metric_list])
        return RegLoaderMetrics(metric_list[0].prefix, mean_loss, mean_r2, metric_list[0].epoch, samples)

    def log_to_mlflow(self) -> None:
        train, val, test = map(self._calculate_mean_metrics, [self.train, self.val, self.test])
        train.log_to_mlflow()
        val.log_to_mlflow()
        if test is not None:
            test.log_to_mlflow()

    def to_result_dict(self) -> Dict:
        return {'metrics': pickle.dumps(self)}

    def reduce(self, reduction='mean'):
        """Reduces LassoNET metrics on hidden_size axis
        i.e. averages over all Lasso models with different l1 regularization parameter 
        or chooses the best model based on validation metrics

        Args:
            reduction (str, optional): If 'mean', then it averages metrics over all lasso models. 
                If 'lassonet_best', then it chooses the best lasso model based on validation r2 and returns just its metrics. 
                Defaults to 'mean'.

        Returns:
            RegMetrics: Returns RegMetrics object with one train, one val and one test RegLoaderMetrics
        """        
        if reduction == 'mean':
            train, val, test = map(self._calculate_mean_metrics, [self.train, self.val, self.test])
            return RegMetrics(train, val, test, epoch=train.epoch)
        elif reduction == 'lassonet_best':
            best_col = int(numpy.argmax([vm.r2 for vm in self.val]))
            self.best_col = best_col
            if self.test is None or len(self.test) == 0:
                return RegMetrics(self.train[best_col], self.val[best_col], None, self.epoch)
            else:
                return RegMetrics(self.train[best_col], self.val[best_col], self.test[best_col], self.epoch)
        else:
            raise ValueError(f'reduction should be one of ["mean", "lassonet_best"]')

    def __str__(self) -> str:

        train, val, test = map(self._calculate_mean_metrics, [self.train, self.val, self.test])
        train_val_str = f'train_loss: {train.loss:.4f}\ttrain_r2: {train.r2:.4f}\tval_loss: {val.loss:.4f}\tval_r2: {val.r2:.4f}'
        if test is not None:
            return train_val_str + f'\ttest_loss: {test.loss:.4f}\ttest_r2: {test.r2:.4f}'
        else:
            return train_val_str
        

@dataclass
class RegFederatedMetrics(Metrics):
    
    clients: List[Metrics]
    epoch: int
    
    def _weighted_mean_metrics(self, metric_list: List[LoaderMetrics]) -> Metrics:
        samples = sum([m.samples for m in metric_list])
        mean_weighted_loss = sum([m.loss*m.samples/samples for m in metric_list])
        mean_weighted_r2 = sum([m.r2*m.samples/samples for m in metric_list])
        return RegLoaderMetrics(metric_list[0].prefix, mean_weighted_loss, mean_weighted_r2, self.epoch, samples)

    @property
    def val_loss(self) -> float:
        return self._weighted_mean_metrics('val', [client.val for client in self.clients]).val_loss

    def reduce(self, reduction='mean'):
        """Reduces metrics from all clients into one metrics aggregated over all clients

        Args:
            reduction (str, optional): If 'mean' then it calculates a weighed mean over clients. 
                If 'lassonet_best', then it averages each lasso model from all clients independently 
                and then it chooses the best model based on aggregated val loss. Defaults to 'mean'.

        Raises:
            ValueError: If reduction not one of the ['mean', 'lassonet_best']
            ValueError: If in case of 'lassonet_best' reduction each of clients metrics does not have a list of Lasso models metrics.

        Returns:
            _type_: _description_
        """        
        if reduction == 'mean':
            reduced_clients = [
                [m.reduce().train for m in self.clients], 
                [m.reduce().val for m in self.clients], 
                [m.reduce().test for m in self.clients]
            ]
            train, val, test = map(self._weighted_mean_metrics, reduced_clients)
            return RegMetrics(train, val, test, self.epoch)

        elif reduction == 'lassonet_best':
            if not isinstance(self.clients[0].train, List):
                raise ValueError(f'for applying lassonet_best reduction each of client.train, client.val, client.test metrics should be a list')
            
            lassonet_metrics = LassoNetRegMetrics([], [], [], epoch=self.epoch)
            for col in range(len(self.clients[0].train)):
                train_col_list = [m.train[col] for m in self.clients]
                val_col_list = [m.val[col] for m in self.clients]
                train, val = map(self._weighted_mean_metrics, [train_col_list, val_col_list])
                lassonet_metrics.train.append(train)
                lassonet_metrics.val.append(val)
                if self.clients[0].test is not None:
                    test_col_list = [m.test[col] for m in self.clients]
                    test = self._weighted_mean_metrics(test_col_list)
                    lassonet_metrics.test.append(test)
    
            return lassonet_metrics
        else:
            raise ValueError('reduction should be one of the ["mean", "lassonet_best"]')

    def log_to_mlflow(self) -> None:
        return self.reduce().log_to_mlflow()

    def to_result_dict(self) -> Dict:
        return self.reduce().to_result_dict()