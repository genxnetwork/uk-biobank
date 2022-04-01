#!/bin/bash
#SBATCH --job-name ukb_local_experiment # good manners rule 
#SBATCH --partition cpu # one of gpu, gpu_devel 
#SBATCH --nodes 1 # amount of nodes allocated 
#SBATCH --time 7:00:00 # hh:mm:ss, walltime 
#SBATCH --mem $MEM 
#SBATCH --cpus-per-task 1
#SBATCH --export ALL

set -o allexport
source /trinity/home/$USER/.mlflow/credentials
set +o allexport

cd /trinity/home/$USER/uk-biobank/src
# remove old parent run id because sometimes clients start faster than server and use old parent run id
rm .mlflow_parent_run_id

singularity exec --nv -B /gpfs/gpfs0/ukb_data,/gpfs/gpfs0/$USER \
  ../image.sif /trinity/home/$USER/.conda/envs/fl/bin/python -u -m local.experiment 
