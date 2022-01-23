import numpy
import pandas
from pgenlib import PgenReader


def load_from_pgen(pfile_path: str, gwas_path: str, snp_count: int) -> numpy.ndarray:
    reader = PgenReader(pfile_path + '.pgen'.encode('utf-8'))
    max_snp_count = reader.get_variant_ct()
    sample_count = reader.get_raw_sample_ct()
    if snp_count > max_snp_count:
        raise ValueError(f'snp_count {snp_count} should be not greater than max_snp_count {max_snp_count}')
    array = numpy.empty((sample_count, snp_count), dtype=numpy.int8)
    if snp_count == max_snp_count:
        reader.read_range(0, max_snp_count, array, sample_maj=True)
    else:
        snp_indices = get_snp_list(pfile_path, gwas_path, snp_count)
        reader.read_list(snp_indices, array, sample_maj=True)
    return array


def load_phenotype(phenotype_path: str) -> numpy.ndarray:
    pass


def get_snp_list(pfile_path: str, gwas_path: str, snp_count: int) -> numpy.ndarray:
    pvar = pandas.read_table(pfile_path + '.pvar')
    gwas = pandas.read_table(gwas_path)
    gwas.sort_values(by='P', axis='index', ascending=False, inplace=True)
    snp_ids = set(gwas.ID.values[:snp_count])
    snp_indices = numpy.arange(pvar.shape[0])[pvar.ID.isin(snp_ids)]
    return snp_indices
    
