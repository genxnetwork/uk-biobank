import os
import sys
import pandas as pd

from preprocess.qc import QC, sample_qc
from preprocess.splitter_tg import SplitTG
from utils.loaders import load_plink_pcs
from utils.plink import run_plink
from utils.split import Split
from preprocess.train_val_split import CVSplitter, WBSplitter
from configs.global_config import sample_qc_ids_path, data_root, TG_BFILE_PATH, \
    TG_SAMPLE_QC_IDS_PATH, TG_DATA_ROOT, TG_OUT, SPLIT_DIR, SPLIT_ID_DIR, SPLIT_GENO_DIR, FOLDS_NUMBER
from configs.pca_config import pca_config_tg
from configs.qc_config import sample_qc_config, variant_qc_config
from configs.split_config import non_iid_split_name, uniform_split_config, split_map, uneven_split_shares_list, \
    TG_SUPERPOP_DICT, NUM_FOLDS

import logging
from os import path, symlink

if __name__ == '__main__':
    # runs the whole pipeline
    logging.basicConfig(level=logging.INFO,
                        stream=sys.stdout,
                        format='%(asctime)s %(levelname)-8s %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S'
                        )
    logger = logging.getLogger()

    # # Generate file with sample IDs that pass central QC with plink
    # logger.info(f'Running sample QC and saving valid ids to {TG_SAMPLE_QC_IDS_PATH}')
    # sample_qc(bin_file_path=TG_BFILE_PATH, output_path=TG_SAMPLE_QC_IDS_PATH, bin_file_type='--pfile')
    #
    # logger.info(f'Running global PCA')
    # os.makedirs(os.path.join(TG_DATA_ROOT, 'pca'), exist_ok=True)
    # PCA().run(input_prefix=TG_BFILE_PATH, pca_config=pca_config_tg,
    #           output_path=os.path.join(TG_DATA_ROOT, 'pca', 'global'),
    #           scatter_plot_path=None,
    #           # scatter_plot_path=os.path.join(TG_OUT, 'global_pca.html'),
    #           bin_file_type='--bfile')

    # Split dataset into IID and non-IID datasets and then QC each local dataset
    logger.info("Splitting ethnic dataset")
    nodes = SplitTG().split(make_pgen=True)

    # for local_prefix in nodes:
    #     logger.info(f'Running local QC for {local_prefix}')
    #     local_prefix_qc = QC.qc(input_prefix=os.path.join(SPLIT_GENO_DIR, local_prefix), qc_config=variant_qc_config)

    logger.info("making k-fold split for the TG dataset")
    superpop_split = Split(SPLIT_DIR, 'ancestry', nodes=nodes)
    splitter = CVSplitter(superpop_split)

    # for node in nodes:
    #     splitter.split_ids(ids_path=os.path.join(SPLIT_GENO_DIR, f'{node}.psam'), node=node, random_state=0)
    #
    # ancestry_df = SplitTG().get_ethnic_background()
    # logger.info(f"Processing split {superpop_split.root_dir}")
    # for node in nodes:
    #     logger.info(f"Saving train, val, test genotypes and running PCA for node {node}")
    #     for fold_index in range(FOLDS_NUMBER):
    #         for part_name in ['train', 'val', 'test']:
    #             ids_path = superpop_split.get_ids_path(node=node, fold_index=fold_index, part_name=part_name)
    #
    #             # Extract and save genotypes
    #             run_plink(args_dict={
    #             '--pfile': superpop_split.get_source_pfile_path(node=node),
    #             '--keep': ids_path,
    #             '--out':  superpop_split.get_pfile_path(node=node, fold_index=fold_index, part_name=part_name)
    #             }, args_list=['--make-pgen'])
    #
    #             # write ancestries aka phenotypes
    #             relevant_ids = ancestry_df['IID'].isin(pd.read_csv(ids_path, sep='\t')['IID'])
    #             ancestry_df.loc[relevant_ids, ['IID', 'ancestry']].to_csv(superpop_split.get_phenotype_path(node=node, fold_index=fold_index, part=part_name), sep='\t', index=False)
    #
    # for fold_index in range(FOLDS_NUMBER):
    #     # Perform centralised sample ids merge to use it with `--keep` flag in plink
    #     ids = []
    #
    #     for node in nodes:
    #         ids_filepath = superpop_split.get_ids_path(
    #             fold_index=fold_index,
    #             part_name='train',
    #             node=node
    #         )
    #
    #         ids.extend(pd.read_csv(ids_filepath, sep='\t')['IID'].to_list())
    #
    #     # Store the list of ids inside the super population split file structure
    #     centralised_ids_filepath = superpop_split.get_ids_path(
    #         fold_index=fold_index,
    #         part_name='train',
    #         node='ALL'  # centralised PCA
    #     )
    #
    #     pd.DataFrame({'IID': ids}).to_csv(centralised_ids_filepath, sep='\t', index=False)

    for fold_index in range(FOLDS_NUMBER):
        logger.info(f'Centralised PCA for fold {fold_index}')
        run_plink(
            args_list=[
                '--pfile', TG_BFILE_PATH,
                '--keep', superpop_split.get_ids_path(fold_index=fold_index, part_name='train', node='ALL'),
                '--freq', 'counts',
                '--out', superpop_split.get_pca_path(node='ALL', fold_index=fold_index, part='train', ext=''),
                '--pca', 'allele-wts', '20'
            ]
        )

        logger.info(f'Projecting train, test, and val parts for each node for fold {fold_index}...')
        for node in nodes:
            for part_name in ['train', 'val', 'test']:
                run_plink(
                    args_list=[
                        '--pfile', superpop_split.get_pfile_path(node=node, fold_index=fold_index, part_name=part_name),
                        '--read-freq', superpop_split.get_pca_path(node='ALL', fold_index=fold_index, part='train', ext='.acount'),
                        '--score', superpop_split.get_pca_path(node='ALL', fold_index=fold_index, part='train', ext='.eigenvec.allele'), '2', '5', 'header-read', 'no-mean-imputation', 'variance-standardize', '--score-col-nums', '6-25',
                        '--out', superpop_split.get_pca_path(node=node, fold_index=fold_index, part=part_name),
                        '--set-missing-var-ids', '@:#'
                    ]
                )
