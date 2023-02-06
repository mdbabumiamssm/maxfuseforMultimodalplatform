# maxfuse benchmark
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix, ConfusionMatrixDisplay

import anndata as ad
import scanpy as sc

import sys
sys.path.append("../../MaxFuse_devo/09302022V/")
import match
import metrics
from scipy.io import mmread
import os

# input chunk
# read in files
out_root = "/ICICLE/output/"
in_root = "/ICICLE/data/"
out_idx = 15

out_dir =out_root + "mf/"
in_dir = in_root 
isExist = os.path.exists(out_dir)
if not isExist:
    os.makedirs(out_dir)
# read in files
atacactivity = mmread(in_dir + "genescore_tea.txt")
atacactivity = pd.DataFrame(atacactivity.todense())
gas_names = pd.read_csv(in_dir + 'genescore_names_tea.csv')['names'].to_numpy()

protein = pd.read_csv(in_dir + "adt.csv")

peak_lsi = pd.read_csv(in_dir + 'lsi_tea_50.csv')
peak_lsi = peak_lsi.drop(['Unnamed: 0','LSI1'], axis=1)

meta = pd.read_csv(in_dir + "atac_meta.csv")

## make anndata
activity_adata = ad.AnnData(
    atacactivity.to_numpy(), dtype=np.float32
)
activity_adata.var_names = gas_names

peak_adata=ad.AnnData(peak_lsi, dtype=np.float32)

protein_adata = ad.AnnData(
    protein.drop(['cell_barcode','total'], axis=1).to_numpy(), dtype=np.float32
)
protein_adata.var_names = protein.drop(['cell_barcode','total'], axis=1).columns

correspondence = pd.read_csv('conversion_v12.csv') # updated names for this
rna_protein_correspondence = []
for i in range(correspondence.shape[0]):
    curr_protein_name, curr_rna_names = correspondence.iloc[i]
    if curr_protein_name not in protein_adata.var_names:
        continue
    if curr_rna_names.find('Ignore') != -1:
        continue
    curr_rna_names = curr_rna_names.split('/')
    for r in curr_rna_names:
        if r in activity_adata.var_names:
            rna_protein_correspondence.append([r, curr_protein_name])
rna_protein_correspondence = np.array(rna_protein_correspondence)

activity_shared = activity_adata[:, rna_protein_correspondence[:, 0]].X
protein_shared = protein_adata[:, rna_protein_correspondence[:, 1]].X

# remove static features for shared
q1 = np.quantile(np.std(activity_shared, axis=0), 0.1)
q2 = np.quantile(np.std(protein_shared, axis=0), 0.05)
print([q1,q2])

# this filtering is best for this dataset and should be used for all methods
mask = ((activity_shared.std(axis=0) > 0.36) & (protein_shared.std(axis=0) > 3.6))
activity_shared = activity_shared[:, mask]
protein_shared = protein_shared[:, mask]

# normalize shared atac counts
activity_shared = ad.AnnData(activity_shared)
sc.pp.normalize_total(activity_shared)
sc.pp.log1p(activity_shared)
sc.pp.scale(activity_shared)
activity_shared = activity_shared.X
#
# protein_shared = protein_shared.copy()
protein_shared = ad.AnnData(protein_shared)
sc.pp.normalize_total(protein_shared)
sc.pp.log1p(protein_shared)
sc.pp.scale(protein_shared)
protein_shared = protein_shared.X

# for all features, protein; atac use peak LSI data
sc.pp.normalize_total(protein_adata)
sc.pp.log1p(protein_adata)
sc.pp.highly_variable_genes(protein_adata)
sc.pp.scale(protein_adata)

# double check if static
protein_active = protein_adata.X
protein_active = protein_active[:, protein_active.std(axis=0) > 1e-5]
#
atac_active = peak_adata.X

spm = match.MaxFuse(
    shared_arr1=protein_shared,
    shared_arr2=activity_shared,
    active_arr1=protein_active,
    active_arr2=atac_active,
    method='centroid_shrinkage',
    labels1=None, # if None, then use scanpy clustering pipeline
    labels2=None
)

#
spm.split_into_batches(
    max_outward_size=5000,
    matching_ratio=5,
    metacell_size=2,
    verbose=True,
    seed =42
)

#
spm.construct_graphs(
    n_neighbors1=15,
    n_neighbors2=15,
    svd_components1=30,
    svd_components2=30,
    resolution1=2,
    resolution2=2,
    randomized_svd=False, 
    svd_runs=1,
    resolution_tol=0.1,
    leiden_runs=1,
    leiden_seed=None,
    verbose=True
)
#
spm.find_initial_pivots(
    wt1=0.5, wt2=0.5,
    svd_components1=20, svd_components2=15,
    randomized_svd=False, svd_runs=1,
    verbose=True
)
#
spm.refine_pivots(
    wt1=0.5, wt2=0.5,
    svd_components1=30, svd_components2=25,
    cca_components=15,
    filter_prop=0.,
    n_iters=3,
    randomized_svd=False, 
    svd_runs=1,
    verbose=True
)
#
spm.filter_bad_matches(target='pivot', filter_prop=0.2, verbose=True)
#
spm.propagate(
    wt1=0.5,
    wt2=0.5,
    svd_components1=30, 
    svd_components2=30, 
    randomized_svd=False, 
    svd_runs=1, 
    verbose=True
)
#
spm.filter_bad_matches(
    target='propagated',
    filter_prop=0.,
    verbose=True
)
#
matching = spm.get_matching(order=(1, 2), target='pivot')
# save out pivot match
pivot = pd.DataFrame(list(zip(matching[0],matching[1])), columns = ["idx1","idx2"])
pivot.to_csv(out_dir + "/pivot_idx.csv", index=False)
#
matching = spm.get_matching(order=(2, 1), target='full_data')
# save metrics file
data = {'method': ['sxc']}  
pd.DataFrame(data).to_csv(out_dir + "/metrics.csv",index=False)
#
full = pd.DataFrame(list(zip(matching[0],matching[1],matching[2])), columns = ["idx1","idx2","score"])
full.to_csv(out_dir + "/full_idx.csv", index=False)
#
arr1_cca, arr2_cca = spm.get_embedding(
    active_arr1 = spm.active_arr1,
    active_arr2 = spm.active_arr2,
    refit=False,
    matching=None,
    order=None,
    cca_components=out_idx,
    cca_max_iter=None
)
#
arr1_df = pd.DataFrame(arr1_cca).iloc[:,0:out_idx]
arr2_df = pd.DataFrame(arr2_cca).iloc[:,0:out_idx]
arr1_df.to_csv(out_dir + "/full_embed_x0.csv",index=False)
arr2_df.to_csv(out_dir + "/full_embed_y0.csv", index=False)
 