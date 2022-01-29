from turtle import forward
from typing import Dict, Any, List, Tuple
from pytorch_lightning import LightningModule
import torch
from torch.nn import Linear, BatchNorm1d
from torch.nn.functional import mse_loss, binary_cross_entropy_with_logits, relu


class BaseNet(LightningModule):
    def __init__(self, input_size: int, l1: float, lr: float, momentum: float, epochs: float) -> None:
        super().__init__()
        self.layer = Linear(input_size, 1)
        self.l1 = l1
        self.lr = lr
        self.momentum = momentum
        self.epochs = epochs

    def forward(self, x):
        out = self.layer(x)
        return out

    def training_step(self, batch: Tuple[torch.Tensor, torch.Tensor], batch_idx: int) -> Dict[str, Any]:
        x, y = batch
        y_hat = self(x)
        loss = self.calculate_loss(y_hat, y)
        if self.l1 is not None:
            loss += self.regularization()
        return {loss: 'loss'}

    def regularization(self):
        return self.l1 * torch.norm(self.layer.weight, p=1)

    def validation_step(self, batch: Tuple[torch.Tensor, torch.Tensor], batch_idx: int) -> Dict[str, Any]:
        x, y = batch
        y_hat = self(x)
        loss = self.calculate_loss(y_hat, y)
        return {'val_loss': loss, 'batch_len': x.shape[0]}

    def calculate_avg_val_metric(self, outputs: List[Dict[str, Any]], metric_name: str) -> float:
        total_len = sum(out['batch_len'] for out in outputs)
        avg_loss = sum(out[metric_name].item()*out['batch_len'] for out in outputs)/total_len
        return avg_loss

    def validation_epoch_end(self, outputs: List[Dict[str, Any]]) -> None:
        avg_loss = self.calculate_avg_val_metric(outputs, 'val_loss')
        self.log('val_loss', avg_loss)

    def configure_optimizers(self):
        optimizer = torch.optim.SGD(self.parameters(), lr=self.lr, momentum=self.momentum)
        scheduler = torch.optim.lr_scheduler.OneCycleLR(optimizer,
                                                        max_lr=self.lr,
                                                        total_steps=self.epochs,
                                                        pct_start=0.1,
                                                        cycle_momentum=False)
        return [optimizer], [scheduler]


class LinearRegressor(BaseNet):
    def __init__(self, input_size: int, l1: float, lr: float, momentum: float, epochs: float) -> None:
        super().__init__()
        self.layer = Linear(input_size, 1)
        self.l1 = l1
        self.lr = lr
        self.momentum = momentum
        self.epochs = epochs

    def calculate_loss(self, y_hat, y):
        return mse_loss(y_hat, y)
    

class LinearClassifier(BaseNet):
    def __init__(self, input_size: int, l1: float, lr: float, momentum: float, epochs: float) -> None:
        super().__init__()
        self.layer = Linear(input_size, 1)
        self.l1 = l1
        self.lr = lr
        self.momentum = momentum
        self.epochs = epochs

    def calculate_loss(self, y_hat: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
        return binary_cross_entropy_with_logits(y_hat, y)
    
    def validation_step(self, batch: Tuple[torch.Tensor, torch.Tensor], batch_idx: int) -> Dict[str, Any]:
        x, y = batch
        y_hat = self(x)
        loss = self.calculate_loss(y_hat, y)
        y_pred = torch.argmax(y_hat, dim=1)
        accuracy = (y_pred == y).float().mean()
        return {'val_loss': loss, 'batch_len': x.shape[0], 'val_accuracy': accuracy}

    def validation_epoch_end(self, outputs: List[Dict[str, Any]]) -> None:
        avg_loss = self.calculate_avg_val_metric(outputs, 'val_loss')
        avg_accuracy = self.calculate_avg_val_accuracy(outputs, 'val_accuracy')
        self.log('val_loss', avg_loss)
        self.log('val_accuracy', avg_accuracy)


class MLPRegressor(BaseNet):
    def __init__(self, input_size: int, hidden_size: int, l1: float, l2: float, lr: float, momentum: float, epochs: float) -> None:
        super().__init__()
        self.input = Linear(input_size, hidden_size)
        self.bn = BatchNorm1d(hidden_size)
        self.hidden = Linear(hidden_size, 1)
        self.l1 = l1
        self.l2 = l2
        self.lr = lr
        self.momentum = momentum
        self.epochs = epochs

    def forward(self, x):
        x = self.relu(self.bn(self.input(x)))
        return self.out(x) 

    def regularization(self):
        reg = 0.0
        if self.l1 is not None:
            reg += self.l1 * torch.norm(self.input.weight, p=1)
        if self.l2 is not None:
            reg += self.l2 * torch.norm(self.input.weight, p=2)
        return reg

    def calculate_loss(self, y_hat, y):
        return mse_loss(y_hat, y)