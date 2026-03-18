#!/usr/bin/env python3
"""
Group-wise ISC Calculation
-----------------------------------------
Modes:
1. Random (--mode random):
   - For each n, randomly sample 2n subjects from the entire pool for each iteration.
   - Calculates run-averaged voxel-wise ISC.

2. Top/Bottom (--mode topbottom):
   - For each n, select the fixed Top 2n and Bottom 2n subjects based on quiz scores.
   - Iterations perform random split-half shuffles on these fixed groups.

Usage:
   python isc_en_cn_topbottom.py --mode random --lang EN --n_iter 30
   python isc_en_cn_topbottom.py --mode topbottom --lang EN --n_iter 30
"""

import os
import re
import pickle
import random
import argparse
import numpy as np
import pandas as pd
from joblib import Parallel, delayed, Memory
from nilearn.maskers import NiftiMasker

# ---------------- Paths & Constants ---------------- #
DATA_ROOT       = '/shared/xinyu/llms_brain_lateralization' 
MASK_EN         = '/shared/data/lpp_average/mask_lpp_en.nii.gz'
MASK_CN         = '/shared/data/lpp_average/mask_lpp_cn.nii.gz'
MASK_FR         = '/shared/data/lpp_average/mask_lpp_fr.nii.gz'
PARTICIPANT_TSV = '/shared/data/lpp-fmri/ds003643/participants.tsv'
OUTPUT_DIR      = '/shared/xinyu/tmp_isc'
CACHE_DIR       = '/shared/xinyu/tmp_isc/cachedir'

LANG_DIRS = {
    "EN": "lpp_en_resampled",
    "CN": "lpp_cn_resampled",
    "FR": "lpp_fr_resampled"
}
LANG_MASKS = {
    "EN": MASK_EN,
    "CN": MASK_CN,
    "FR": MASK_FR,
}

RUNS   = 9
TR     = 2.0
N_LIST = [4, 8, 12, 16, 20, 24]

os.makedirs(OUTPUT_DIR, exist_ok=True)
memory = Memory(CACHE_DIR, verbose=0)

# ---------------- Helper Functions ---------------- #

def get_available_subjects(lang):
    """Scan the directory to find available subject IDs."""
    p = re.compile(rf'sub-{lang}(\d{{3}})')
    lang_dir = os.path.join(DATA_ROOT, LANG_DIRS[lang])
    if not os.path.exists(lang_dir):
        raise FileNotFoundError(f"Directory not found: {lang_dir}")
    return sorted(int(m[1]) for s in os.listdir(lang_dir) if (m := p.match(s)))

def get_data_path(lang, sub, run):
    """Construct file path for a specific subject run."""
    label = f'sub-{lang}{sub:03d}'
    return os.path.join(DATA_ROOT, LANG_DIRS[lang],
                        label, f'{label}_run{run+1}.nii.gz')

@memory.cache(ignore=['masker'])
def load_and_preproc(lang, sub, run, masker, mask_tag):
    """Load Nifti file and apply masker (cached)."""
    fp = get_data_path(lang, sub, run)
    if not os.path.exists(fp):
        return None
    # Remove first and last 10 TRs
    return masker.transform(fp)[10:-10]

def compute_run_average(lang, subs, run, masker, mask_tag):
    """Load data for a group of subjects and average them for a specific run."""
    stack = [load_and_preproc(lang, s, run, masker, mask_tag) for s in subs]
    stack = [x for x in stack if x is not None]
    if not stack:
        raise ValueError(f"Missing data for run {run+1}")
    return np.mean(stack, axis=0)

def voxelwise_corr(a, b):
    """Compute column-wise (voxel-wise) correlation between two 2D arrays."""
    with np.errstate(invalid='ignore'):
        # a, b shape: (n_timepoints, n_voxels)
        r = np.array([np.corrcoef(a[:, v], b[:, v])[0, 1] 
                      for v in range(a.shape[1])], dtype=np.float32)
    r[np.isnan(r)] = 0.0
    return r

def groupwise_isc_once(lang, subs, masker, mask_tag):
    """
    Core calculation:
    1. Shuffle subjects
    2. Split into two halves
    3. Compute average timecourse for each half per run
    4. Correlate and average across runs
    """
    # Ensure we work on a copy to avoid side effects if list is reused
    subs = subs.copy() 
    random.shuffle(subs)
    mid = len(subs) // 2
    
    corrs = []
    for r in range(RUNS):
        d1 = compute_run_average(lang, subs[:mid], r, masker, mask_tag)
        d2 = compute_run_average(lang, subs[mid:], r, masker, mask_tag)
        corrs.append(voxelwise_corr(d1, d2))
    
    return {'ids': subs, 'isc': np.mean(corrs, axis=0)}

def build_ranked_lists(lang, seed):
    """Parse TSV, handle ties, and return sorted subject IDs."""
    df = pd.read_csv(PARTICIPANT_TSV, sep='\t')
    df = df[df['participant_id'].str.contains(lang)]
    df['score'] = pd.to_numeric(df['correct_quiz_questions'], errors='coerce')
    df = df.dropna(subset=['score'])
    df['sid'] = df['participant_id'].str.extract(r'(\d{3})').astype(int)
    
    # Random tie-breaking
    rng = np.random.default_rng(seed)
    df['tie'] = rng.random(len(df))
    
    top = (df.sort_values(['score', 'tie'], ascending=[False, True])['sid'].tolist())
    bottom = (df.sort_values(['score', 'tie'], ascending=[True, True])['sid'].tolist())
    return top, bottom

# ---------------- Main Logic ---------------- #

def main():
    parser = argparse.ArgumentParser(description="Unified Group-wise ISC")
    parser.add_argument('--mode', type=str, required=True, choices=['random', 'topbottom'],
                        help="Execution mode: 'random' sampling or 'topbottom' ranking")
    parser.add_argument('--seed', type=int, default=1, help='Global RNG seed')
    parser.add_argument('--n_iter', type=int, default=30, help='Iterations per n')
    parser.add_argument('--lang', type=str, default='EN', choices=['EN', 'CN', 'FR'],
                        help='Language (EN/CN/FR)')
    args = parser.parse_args()

    # Set seeds
    random.seed(args.seed)
    np.random.seed(args.seed)

    print(f"--- Starting: Mode={args.mode}, Lang={args.lang}, Seed={args.seed} ---")

    # Initialize language-specific masker
    mask_img = LANG_MASKS[args.lang]
    if not os.path.exists(mask_img):
        raise FileNotFoundError(f"Mask not found for {args.lang}: {mask_img}")
    # cache key marker to prevent stale cache reuse when mask changes
    mask_tag = os.path.basename(mask_img)
    masker = NiftiMasker(mask_img=mask_img, smoothing_fwhm=8,
                         detrend=True, standardize=True,
                         low_pass=0.2, high_pass=0.01,
                         t_r=TR, memory='nilearn_cache',
                         memory_level=1, verbose=0).fit()

    # Get available subjects
    avail_subjects = set(get_available_subjects(args.lang))
    avail_list = sorted(list(avail_subjects))
    
    if len(avail_list) < 2 * max(N_LIST):
        print(f"Warning: Only {len(avail_list)} subjects available. "
              f"Max N={max(N_LIST)} requires {2*max(N_LIST)} subjects.")

    results = {}

    # ---------------- MODE: RANDOM ---------------- #
    if args.mode == 'random':
        for n in N_LIST:
            need = 2 * n
            if len(avail_list) < need:
                print(f"[Skip] n={n} (need {need}, have {len(avail_list)})")
                continue

            print(f"n={n}: random sampling x {args.n_iter}")

            def one_iter_random(_):
                # Critical: Sample NEW subjects for every iteration
                pool = random.sample(avail_list, need)
                return groupwise_isc_once(args.lang, pool, masker, mask_tag)

            results[n] = Parallel(n_jobs=-1)(
                delayed(one_iter_random)(i) for i in range(args.n_iter)
            )

    # ---------------- MODE: TOP/BOTTOM ---------------- #
    elif args.mode == 'topbottom':
        # Get ranked lists
        top_all, bottom_all = build_ranked_lists(args.lang, args.seed)
        top_all = [s for s in top_all if s in avail_subjects]
        bottom_all = [s for s in bottom_all if s in avail_subjects]
        
        results = {'top': {}, 'bottom': {}}

        for n in N_LIST:
            need = 2 * n
            if len(top_all) < need or len(bottom_all) < need:
                print(f"[Skip] n={n} (need {need}, have Top:{len(top_all)}/Bot:{len(bottom_all)})")
                continue
            
            # Slice fixed pools
            top_pool = top_all[:need]
            bottom_pool = bottom_all[:need]

            print(f"n={n}: computing TOP & BOTTOM ...")

            def one_iter_fixed(pool):
                # Pool is fixed, just reshuffling inside groupwise_isc_once
                return groupwise_isc_once(args.lang, pool, masker, mask_tag)

            # Parallel execution for Top
            results['top'][n] = Parallel(n_jobs=-1)(
                delayed(one_iter_fixed)(top_pool) for _ in range(args.n_iter)
            )
            # Parallel execution for Bottom
            results['bottom'][n] = Parallel(n_jobs=-1)(
                delayed(one_iter_fixed)(bottom_pool) for _ in range(args.n_iter)
            )

    # ---------------- Save Results ---------------- #
    out_name = f"isc_{args.lang.lower()}_{args.mode}_n{','.join(map(str, N_LIST))}_" \
               f"{args.n_iter}iter_seed{args.seed}.pkl"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    
    with open(out_path, 'wb') as f:
        pickle.dump(results, f)
    
    print(f"Done. Saved to -> {out_path}")

if __name__ == "__main__":
    main()
