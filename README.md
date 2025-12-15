# lpp_isc_top_bottom (Group-wise ISC Calculation)


### Modes

#### 1. Random (`--mode random`)
- For each *n*, randomly sample **2n subjects** from the entire pool for each iteration.
- Compute **run-averaged, voxel-wise ISC**.

#### 2. Top/Bottom (`--mode topbottom`)
- For each *n*, select **fixed Top 2n** and **Bottom 2n** subjects based on quiz scores.
- Iterations perform **random split-half shuffles** within these fixed groups.

---

### Usage

```bash
python unified_isc.py --mode random --lang EN --n_iter 30
python unified_isc.py --mode topbottom --lang EN --n_iter 30
```

---


### Result

<p align="center">
  <img src="lpp_isc_top_bottom/english.png" width="70%">
</p>

<p align="center">
  <img src="lpp_isc_top_bottom/chinese.png" width="70%">
</p>

