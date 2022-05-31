#!/bin/env python

#SBATCH --job-name=multiprocess
#SBATCH --output=logs/multiprocess_%j.out
#SBATCH --error=logs/multiprocess_%j.err
#SBATCH --time=00:20:00
#SBATCH --partition=gpu_devel
#SBATCH --nodes=1
#SBATCH --gpus=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem 26000

import multiprocessing
import sys
import os
from socket import gethostname
from omegaconf import DictConfig, OmegaConf
import mlflow
import hashlib
import logging

from fl.node_process import MlflowInfo, Node, TrainerInfo
from fl.server_process import Server


# necessary to add cwd to path when script run 
# by slurm (since it executes a copy)

sys.path.append(os.getcwd()) 


def get_cfg_hash(cfg: DictConfig):
    yaml_representation = OmegaConf.to_yaml(cfg)
    return hashlib.sha224(yaml_representation.encode()).hexdigest()


NODE_RESOURCES = {
    '0': {'partition': 'cpu', 'mem_mb': 8000, 'gpus': 0},
    '1': {'partition': 'cpu', 'mem_mb': 8000, 'gpus': 0},
    '2': {'partition': 'gpu', 'mem_mb': 64000, 'gpus': 1},
    '3': {'partition': 'gpu', 'mem_mb': 36000, 'gpus': 1},
    '4': {'partition': 'gpu', 'mem_mb': 24000, 'gpus': 1},
    '5': {'partition': 'gpu', 'mem_mb': 16000, 'gpus': 1},
    '6': {'partition': 'cpu', 'mem_mb': 8000, 'gpus': 0},
    '7': {'partition': 'cpu', 'mem_mb': 8000, 'gpus': 0},
}


def configure_logging():
    # loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict]
    all_names = [name for name in logging.root.manager.loggerDict]
    print('logger names:')
    print(all_names)
    names = ['flower', 'pytorch_lightning']
    for name in names:
        logger = logging.getLogger(name)
        logger.handlers = []


if __name__ == '__main__':
    
    configure_logging()
    # parse command-line runner.py arguments
    args = OmegaConf.from_cli(sys.argv)
    queue = multiprocessing.Queue()
    cfg_path = 'src/fl/configs/mlp.yaml'
    server_url = f'{gethostname()}:8080'
    log_dir = f'logs/job-{os.environ["SLURM_JOB_ID"]}'
    os.makedirs(log_dir, exist_ok=True)

    # command-line arguments take precedents over config parameters
    mlflow_url = os.environ.get('MLFLOW_TRACKING_URI', './mlruns')
    print(f'logging mlflow data to server {mlflow_url}')
    
    cfg = OmegaConf.merge(OmegaConf.load(cfg_path), args)
    experiment = mlflow.set_experiment(cfg.experiment.name)

    params_hash = get_cfg_hash(cfg)
    with mlflow.start_run(
        experiment_id=experiment.experiment_id,
        tags={
            'description': cfg.experiment.description,
            'params_hash': params_hash,
        }
    ) as run:
        mlflow.log_params(cfg.server)
        info = MlflowInfo(experiment.experiment_id, run.info.run_id)

        # assigning gpus to nodes and creating process objects
        gpu_index = -1
        for node_index in cfg.server.strategy.nodes:
            need_gpu = NODE_RESOURCES[str(node_index)]['gpus']
            if need_gpu:
                gpu_index += 1
                trainer_info = TrainerInfo([gpu_index], 'gpu', node_index)
            else:
                trainer_info = TrainerInfo(1, 'cpu', node_index)
            node = Node(server_url, log_dir, info, queue, cfg, trainer_info)
            node.start()
            print(f'starting node {node_index}')
        
        # create, start and wait for server to finish 
        server = Server(log_dir, queue, params_hash, cfg)
        server.start()
        server.join()
        
        # wait for all nodes to finish
        for node_index in cfg.server.strategy.nodes:
            node.join()
        print(f'Nodes are finished')