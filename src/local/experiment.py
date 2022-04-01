import hydra
import logging
from sys import stdout
from omegaconf import DictConfig
import mlflow
from mlflow.xgboost import autolog
from numpy import hstack
from sklearn.linear_model import LassoCV
from xgboost import XGBRegressor
from sklearn.metrics import r2_score


from fl.datasets.memory import load_covariates, load_phenotype, load_from_pgen, get_sample_indices

class LocalExperiment():
    """
    Base class for experiments in a local setting
    """
    def __init__(self, cfg: DictConfig):
        """
        Args:
            cfg: Configuration for experiments from hydra
        """
        self.cfg = cfg
        logging.basicConfig(level=logging.INFO,
                        stream=stdout,
                        format='%(asctime)s %(levelname)-8s %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S'
                        )
        self.logger = logging.getLogger()
            
    def start_mlflow_run(self):
        mlflow.set_experiment(f'local_{self.cfg.model.name}')
        feature_string = f'{self.cfg.experiment.snp_count} SNPs ' if self.cfg.experiment.include_genotype  \
            else '' + 'covariates' if self.cfg.experiment.include_covariates else ''
        self.run = mlflow.start_run(tags={
                            'name': self.cfg.model.name,
                            'split': self.cfg.split_dir.split('/')[-1],
                            'phenotype': self.cfg.phenotype.name,
                            'node_index': str(self.cfg.node_index),
                            'features': feature_string,
                            'snp_count': str(self.cfg.experiment.snp_count),
                            'gwas_path': self.cfg.data.gwas}
                            )

    def load_data(self):
        self.logger.info("Loading data")
        
        self.y_train = load_phenotype(self.cfg.data.phenotype.train)
        self.y_val = load_phenotype(self.cfg.data.phenotype.val)
        self.y_test = load_phenotype(self.cfg.data.phenotype.test)
        
        assert self.cfg.experiment.include_genotype or self.cfg.experiment.include_covariates
        
        if self.cfg.experiment.include_genotype and self.cfg.experiment.include_covariates:
            self.load_genotype_and_covariates_()    
        elif self.cfg.experiment.include_genotype:
            self.load_genotype_()
        else:
            self.load_covariates()
            
        self.logger.info(f"{self.X_train.shape[1]} features loaded")
        
    def load_sample_indices(self):
        self.logger.info("Loading sample indices")
        self.sample_indices_train = get_sample_indices(self.cfg.data.genotype.train,
                                                       self.cfg.data.phenotype.train)
        self.sample_indices_val = get_sample_indices(self.cfg.data.genotype.val,
                                                     self.cfg.data.phenotype.val)
        self.sample_indices_test = get_sample_indices(self.cfg.data.genotype.test,
                                                      self.cfg.data.phenotype.test)
        
    def load_genotype_and_covariates_(self):
        self.X_train = hstack((load_from_pgen(self.cfg.data.genotype.train,
                                              self.cfg.data.gwas,
                                              snp_count=self.cfg.experiment.snp_count,
                                              sample_indices=self.sample_indices_train),
                               load_covariates(self.cfg.data.covariates.train)))
        self.X_val = hstack((load_from_pgen(self.cfg.data.genotype.val,
                                              self.cfg.data.gwas,
                                              snp_count=self.cfg.experiment.snp_count,
                                              sample_indices=self.sample_indices_val),
                               load_covariates(self.cfg.data.covariates.val)))
        self.X_test = hstack((load_from_pgen(self.cfg.data.genotype.test,
                                              self.cfg.data.gwas,
                                              snp_count=self.cfg.experiment.snp_count,
                                              sample_indices=self.sample_indices_test),
                               load_covariates(self.cfg.data.covariates.test)))
        
    def load_genotype_(self):
        self.X_train = load_from_pgen(self.cfg.data.genotype.train,
                                      self.cfg.data.gwas,
                                      snp_count=self.cfg.experiment.snp_count,
                                      sample_indices=self.sample_indices_train)
        self.X_val = load_from_pgen(self.cfg.data.genotype.val,
                                    self.cfg.data.gwas,
                                    snp_count=self.cfg.experiment.snp_count,
                                    sample_indices=self.sample_indices_val)
        self.X_test = load_from_pgen(self.cfg.data.genotype.test,
                                     self.cfg.data.gwas,
                                     snp_count=self.cfg.experiment.snp_count,
                                     sample_indices=self.sample_indices_test)
        
    def load_covariates_(self):
        self.X_train = load_covariates(self.cfg.data.covariates.train)
        self.X_val = load_covariates(self.cfg.data.covariates.val)
        self.X_test = load_covariates(self.cfg.data.covariates.test)
    
    def train(self):
        pass
        
    def eval_and_log(self):
        self.logger.info("Evaluating model")
        preds_train = self.model.predict(self.X_train)
        preds_val = self.model.predict(self.X_val)
        preds_test = self.model.predict(self.X_test)

        r2_train = r2_score(self.y_train, preds_train)
        r2_val = r2_score(self.y_val, preds_val)
        r2_test = r2_score(self.y_test, preds_test)

        print(f"Train r2: {r2_train}")
        mlflow.log_metric('train_r2', r2_train)
        print(f"Val r2: {r2_val}")
        mlflow.log_metric('val_r2', r2_val)
        print(f"Test r2: {r2_test}")
        mlflow.log_metric('test_r2', r2_test)
    
    def run(self):
        self.load_sample_indices()
        self.load_data()
        self.start_mlflow_run()
        self.train()
        self.eval_and_log()
    

def simple_estimator_factory(model):
    """Returns a SimpleEstimatorExperiment for a given model class, expected
    to have the same interface as scikit-learn estimators.
    
    Args:
        model: Model class
        model_kwargs_dict: Dictionary of parameters passed during model initialization
    """
    class SimpleEstimatorExperiment(LocalExperiment):
        def __init__(self, cfg):
            LocalExperiment.__init__(self, cfg)
            self.model = model(**self.cfg.model.params)

        def train(self):
            self.logger.info("Training")
            self.model.fit(self.X_train, self.y_train)
       
    return SimpleEstimatorExperiment

class XGBExperiment(LocalExperiment):
    def __init__(self, cfg):
        LocalExperiment.__init__(self, cfg)
        self.model = XGBRegressor(**self.cfg.model.params)

    def train(self):
        self.logger.info("Training")
        autolog()
        self.model.fit(self.X_train, self.y_train, eval_set=[(self.X_val, self.y_val)], early_stopping_rounds=5, verbose=True)

# Dict of possible experiment types and their corresponding classes
experiment_dict = {
    'lasso': simple_estimator_factory(LassoCV),
    'xgboost': XGBExperiment
    
}

            
@hydra.main(config_path='configs', config_name='default')
def local_experiment(cfg: DictConfig):
    assert cfg.model.name in experiment_dict.keys()
    experiment = experiment_dict[cfg.model.name](cfg)
    experiment.run()   
    
if __name__ == '__main__':
    local_experiment()
