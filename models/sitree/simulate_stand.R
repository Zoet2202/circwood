# Simulate one Norway-spruce stand with SiTree and export the growth curve the
# rotation model reads (models/rotation/growth_curve.csv) — ROADMAP Phase L.
#
# Reproduces the set-up of Landaas (2023): the package's own example stand
# `stand.west.tr` / `stand.west.st` (651 Norway spruce on 1 019 m2 in four plots,
# site index 14, municipality 1201 Bergen, spacing 1.25 x 1.25 m), grown for
# 250 years in 5-year periods without thinning or harvest, with the diameter
# increment of Bollandsås & Næsset (2009) (`dbhi.BN2009`) and Korf height
# increment (`height.korf`) through `grow.dbhinc.hgtinc`, the mortality of
# Bollandsås (2007) (`mort.B2007`) and the recruitment of Bollandsås, Buongiorno
# & Gobakken (2008) (`recr.BBG2008`) — the call the package documents for this
# stand (`?sitree`, second example), run to the thesis's horizon.
#
# Volume is `volume.norway` (the national forest inventory's single-tree volume
# functions) for every tree alive in a period, summed and scaled to one hectare
# with the stand's own `tree2ha`; stem volume *without bark*, the roundwood
# measure the Circwood dataset uses (with bark is about a tenth higher). Trees
# found dead in a period enter a dead-wood pool at their volume at death
# (`last.measurement`) and the pool loses 4 % per period (Yatskov, Harmon &
# Krankina 2003; the thesis's assumption, 0.008/yr in parameters.csv).
#
# Mortality and recruitment are stochastic, so the export is the mean of
# N_REPLICATES runs (seed 2017 — the one the package's example uses — and the
# following integers); the spread is written beside it. Five-year periods are
# interpolated linearly to the annual grid the rotation model needs.
#
# `age_years` counts from the start of the simulation, as the thesis's periods
# do (its Faustmann optimum "t = 18" is 90 years of simulation). The example
# stand is already 0.8–5.3 m tall at t0 — roughly 15–20 years old on a site
# index 14 height curve — so stand ages are that much higher; the offset is
# stated here and in METHODS, not applied, so the numbers stay comparable with
# the thesis's own.
#
# Requirements: R >= 4.1 and `install.packages("sitree")` (CRAN, NIBIO; GPL).
# Executed with sitree 0.1.15 on R 4.6.2 (Windows) on 2026-11-08; about a minute.
# Run from anywhere:
#     Rscript models/sitree/simulate_stand.R
#
# Output contract (what models/rotation/rotation.py reads): one row per year on
# an annual grid, columns age_years, volume_m3_ha, dead_volume_m3_ha; further
# columns (volume_sd_m3_ha, stems_per_ha) are informative and ignored; `#`
# comment lines at the top.

suppressPackageStartupMessages(library(sitree))

PERIOD_LENGTH         <- 5
N_PERIODS             <- 50                    # 250 years, the thesis's horizon
N_REPLICATES          <- 10
SEEDS                 <- 2017 + seq_len(N_REPLICATES) - 1
DEAD_DECAY_PER_PERIOD <- 0.04                  # share of the dead pool lost per 5-year period
BARK                  <- "without"             # "without": stem volume under bark; "with": with bark

args   <- commandArgs(trailingOnly = FALSE)
script <- sub("^--file=", "", args[grepl("^--file=", args)])
root   <- if (length(script)) normalizePath(file.path(dirname(script), "..", "..")) else getwd()
OUT    <- file.path(root, "models", "sitree", "growth_curve_sitree.csv")

tree.df  <- stand.west.tr
stand.df <- stand.west.st
tree2ha  <- unique(stand.df$tree2ha)                       # 9.81: one tree on the 1 019 m2 stand per ha
stopifnot(length(tree2ha) == 1)
kom_of_plot <- setNames(stand.df$kom, stand.df$plot.id)

#' Stem volume (m3) of every tree alive in the period; 0 otherwise. After the
#' simulation SiTree returns every tree that was ever alive, with dbh and height
#' 0 (not NA) in the periods before recruitment and after death.
tree_volume_m3 <- function(dbh_mm, height_dm, tree_sp, kom) {
  alive <- !is.na(dbh_mm) & !is.na(height_dm) & dbh_mm > 0 & height_dm > 0
  out <- numeric(length(dbh_mm))
  if (any(alive)) {
    small <- alive & dbh_mm < 50                           # volume.norway's domain starts at 50 mm
    dbh_mm[small] <- 50
    v <- volume.norway(dbh.mm = dbh_mm[alive], height.dm = height_dm[alive],
                       tree.sp = tree_sp[alive], kom = kom[alive])
    vol <- if (BARK == "without") v$vol.wo.tr.m3 else v$vol.w.tr.m3
    attr(out, "no_volume") <- sum(is.na(vol))              # outside the volume function's domain
    vol[is.na(vol)] <- 0
    out[alive] <- vol
    attr(out, "clamped") <- sum(small)
  }
  out
}

simulate_once <- function(seed) {
  set.seed(seed)
  res <- sitree(
    tree.df   = tree.df,
    stand.df  = stand.df,
    functions = list(
      fn.growth           = "grow.dbhinc.hgtinc",
      fn.mort             = "mort.B2007",
      fn.recr             = "recr.BBG2008",
      fn.management       = NULL,                          # no thinning, no harvest
      fn.tree.removal     = NULL,
      fn.modif            = NULL,
      fn.prep.common.vars = "prep.common.vars.fun"
    ),
    n.periods      = N_PERIODS,
    period.length  = PERIOD_LENGTH,
    mng.options    = NA,
    print.comments = FALSE,
    fn.dbh.inc     = "dbhi.BN2009",
    fn.hgt.inc     = "height.korf"
  )
  periods <- paste0("t", 0:N_PERIODS)
  live <- res$live$data                                    # every tree alive at any point; NA when not alive
  kom_live <- kom_of_plot[as.character(live$plot.id)]
  clamped <- 0
  no_volume <- 0
  live_m3_ha <- vapply(periods, function(p) {
    v <- tree_volume_m3(live$dbh.mm[[p]], live$height.dm[[p]], live$tree.sp, kom_live)
    clamped <<- clamped + (attr(v, "clamped") %||% 0)
    no_volume <<- no_volume + (attr(v, "no_volume") %||% 0)
    sum(v) * tree2ha
  }, numeric(1))
  stems_ha <- vapply(periods, function(p) sum(live$dbh.mm[[p]] > 0, na.rm = TRUE) * tree2ha, numeric(1))
  # Basal area per ha: the realism check (the example stand is planted at 6 400 stems/ha).
  ba_ha <- vapply(periods, function(p) {
    d <- live$dbh.mm[[p]]
    sum(pi * (d[!is.na(d) & d > 0] / 2000)^2) * tree2ha
  }, numeric(1))

  # Dead trees: volume at death enters the pool in the period they were found dead.
  lm   <- res$dead$last.measurement
  dead <- res$dead$data
  idx  <- match(lm$treeid, dead$treeid)
  v_dead <- tree_volume_m3(lm$dbh.mm, lm$height.dm, dead$tree.sp[idx],
                           kom_of_plot[as.character(dead$plot.id[idx])])
  dead_in <- vapply(periods, function(p) sum(v_dead[as.character(lm$found.dead) == p]) * tree2ha, numeric(1))
  pool <- numeric(length(periods))
  for (i in seq_along(periods)) {
    pool[i] <- (if (i == 1) 0 else pool[i - 1] * (1 - DEAD_DECAY_PER_PERIOD)) + dead_in[i]
  }
  list(live = live_m3_ha, dead = pool, stems = stems_ha, ba = ba_ha, dead_in = dead_in, clamped = clamped,
       no_volume = no_volume, n_trees = nrow(lm), n_recruits = length(live$treeid) - nrow(tree.df))
}

`%||%` <- function(a, b) if (is.null(a)) b else a

t0 <- Sys.time()
runs <- lapply(SEEDS, simulate_once)
cat(sprintf("%d replicates in %.0f s\n", N_REPLICATES, as.numeric(difftime(Sys.time(), t0, units = "secs"))))

live_mat <- sapply(runs, `[[`, "live")
dead_mat <- sapply(runs, `[[`, "dead")
stem_mat <- sapply(runs, `[[`, "stems")
ba_mat   <- sapply(runs, `[[`, "ba")
live_mean <- rowMeans(live_mat)
live_sd   <- apply(live_mat, 1, sd)
dead_mean <- rowMeans(dead_mat)
stem_mean <- rowMeans(stem_mat)
ba_mean   <- rowMeans(ba_mat)

ages_period <- (0:N_PERIODS) * PERIOD_LENGTH
ages        <- 0:(N_PERIODS * PERIOD_LENGTH)
curve <- data.frame(
  age_years         = ages,
  volume_m3_ha      = round(approx(ages_period, live_mean, xout = ages)$y, 4),
  dead_volume_m3_ha = round(approx(ages_period, dead_mean, xout = ages)$y, 4),
  volume_sd_m3_ha   = round(approx(ages_period, live_sd,   xout = ages)$y, 4),
  stems_per_ha      = round(approx(ages_period, stem_mean, xout = ages)$y, 1),
  basal_area_m2_ha  = round(approx(ages_period, ba_mean,   xout = ages)$y, 2)
)

peak_age <- ages_period[which.max(live_mean)]
header <- c(
  sprintf(paste0(
    "SiTree %s on R %s: example stand stand.west.tr / stand.west.st (%d Norway spruce, %.0f m2, SI %s, kom %s) ",
    "grown %d x %d years without management - grow.dbhinc.hgtinc (dbhi.BN2009, height.korf), mort.B2007, ",
    "recr.BBG2008, prep.common.vars.fun; the call documented in ?sitree for this stand, run to the thesis's horizon."),
    as.character(packageVersion("sitree")), paste(R.version$major, R.version$minor, sep = "."),
    nrow(tree.df), 1e4 / tree2ha, unique(stand.df$SI.m), unique(stand.df$kom), N_PERIODS, PERIOD_LENGTH),
  sprintf(paste0(
    "volume_m3_ha: volume.norway stem volume %s bark of every tree alive, x tree2ha %.2f; dead_volume_m3_ha: pool of ",
    "trees found dead at their volume at death, losing %.0f %% per period. Mean of %d replicates (seeds %d-%d; ",
    "mortality and recruitment are stochastic), linearly interpolated from 5-year periods to years; volume_sd_m3_ha ",
    "is the spread across replicates; stems_per_ha is informative."),
    BARK, tree2ha, 100 * DEAD_DECAY_PER_PERIOD, N_REPLICATES, min(SEEDS), max(SEEDS)),
  sprintf(paste0(
    "age_years counts from the simulation start, as the thesis's periods do; the stand is 0.8-5.3 m tall at t0 ",
    "(about 15-20 years old), so stand ages are that much higher. Mean living volume peaks at %.0f m3/ha at ",
    "%d years; %.0f m3/ha at 250 years; %.0f stems/ha at t0."),
    max(live_mean), peak_age, live_mean[length(live_mean)], stem_mean[1]),
  sprintf("Written by models/sitree/simulate_stand.R on %s; %d trees died and %d were recruited per replicate on average.",
          format(Sys.Date()), round(mean(sapply(runs, `[[`, "n_trees"))), round(mean(sapply(runs, `[[`, "n_recruits"))))
)
header <- c(header, sprintf(paste0(
  "Realism check: basal area %.0f m2/ha at t0, %.0f at 50 years, %.0f at 150 years (a real spruce stand rarely exceeds ",
  "70): the 6 400 stems/ha of the example stand lie outside the calibration range of the increment and mortality ",
  "functions, so the absolute volumes are not credible; the relative growth (the shape) is what the rotation model ",
  "compares against its calibrated logistic curve."), ba_mean[1], ba_mean[11], ba_mean[31]))
clamped_total <- sum(sapply(runs, `[[`, "clamped"))
no_volume_total <- sum(sapply(runs, `[[`, "no_volume"))
if (clamped_total > 0 || no_volume_total > 0) {
  header <- c(header, sprintf(paste0(
    "%d tree-period volumes used dbh 50 mm for a smaller recruit and %d tree-periods got no volume from ",
    "volume.norway (counted as 0), out of %d live tree-periods across the replicates."),
    clamped_total, no_volume_total, round(sum(stem_mat) / tree2ha)))
}

con <- file(OUT, open = "wb")                               # LF line endings, like every committed CSV
writeLines(paste0("# ", header), con, sep = "\n")
write.table(curve, con, sep = ",", row.names = FALSE, col.names = TRUE, quote = FALSE, eol = "\n")
close(con)
cat("wrote", OUT, "\n")
cat(sprintf("living volume: t0 %.1f, 50 y %.1f, 90 y %.1f, 135 y %.1f, 250 y %.1f m3/ha (sd at 90 y %.1f)\n",
            live_mean[1], live_mean[11], live_mean[19], live_mean[28], live_mean[51], live_sd[19]))
cat(sprintf("dead pool: 90 y %.1f, 250 y %.1f m3/ha; stems/ha t0 %.0f, 250 y %.0f; basal area m2/ha t0 %.0f, 50 y %.0f, 150 y %.0f, 250 y %.0f\n",
            dead_mean[19], dead_mean[51], stem_mean[1], stem_mean[51], ba_mean[1], ba_mean[11], ba_mean[31], ba_mean[51]))
