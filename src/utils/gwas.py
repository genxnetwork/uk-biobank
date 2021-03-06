from typing import List, Set, Tuple
import pandas


def read_all_gwas(gwas_sources: List[str]) -> List[pandas.DataFrame]:
    """
    Reads all GWAS results for a particular phenotype and split from {gwas_dir}

    Args:
        gwas_sources (List[str]): List of files where GWAS results are located for all nodes from particular fold  

    Returns:
        List[pandas.DataFrame]: List of GWAS results with #CHROM, POS, LOG10_P columns and ID as index 
    """    
    results = []
    for gwas_path in gwas_sources:
        gwas = pandas.read_table(gwas_path)
        results.append(gwas.loc[:, ['#CHROM', 'POS', 'ID', 'LOG10_P']].set_index('ID'))
    return results


def get_topk_snps(gwas: pandas.DataFrame, max_snp_count: int) -> pandas.DataFrame:
    """Returns top {max_count_snps} SNP IDs from {gwas} GWAS results

    Args:
        gwas (pandas.DataFrame): plink 2.0 GWAS output with LOG10_P values
        max_snp_count (int): Number of the most significant SNPs to return

    Returns:
        pandas.DataFrame: DataFrame with {max_snp_count} most significant SNPs where index is SNP ID 
    """    
    sorted_gwas = gwas.sort_values(by='LOG10_P', axis='index', ascending=False).iloc[:max_snp_count, :]
    return sorted_gwas.drop('LOG10_P', axis='columns')

