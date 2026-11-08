# Simulate the thesis's stand with SiTree and export its growth curve
# (models/sitree/growth_curve_sitree.csv) — ROADMAP Phase L.
#
# Reproduces the set-up of Landaas (2023): the package's own example stand
# `stand.west.tr` / `stand.west.st` (651 Norway spruce on 1 019 m2 in four plots,
# site index 14, municipality 1201 Bergen, spacing 1.25 x 1.25 m), grown for
# 250 years in 5-year periods without thinning or harvest with the functions
# the package documents for it (see sitree_common.R). Volume is `volume.norway`
# stem volume *without bark*, the roundwood measure the Circwood dataset uses.
#
# `age_years` counts from the start of the simulation, as the thesis's periods
# do (its Faustmann optimum "t = 18" is 90 years of simulation). The example
# stand is already stocked at t0 — 187 m3/ha, 6 400 stems/ha, dominant height
# 13–14 m, about 40 years old on a site index 14 height curve — so stand ages
# are that much higher; the rotation model adds the offset
# (`sitree_stand_age_at_start_years` in models/rotation/parameters.csv).
#
# Realism: at 6 400 stems/ha the increment and mortality functions are
# extrapolated far outside their calibration range (basal area 186 m2/ha at 50
# simulated years, 276 at 150; a real spruce stand rarely exceeds 70), so the
# absolute volumes are not credible and the curve is kept as a comparison. The
# SSB-anchored stand in simulate_ssb_stand.R is the realistic alternative.
#
# Requirements: R >= 4.1 and `install.packages("sitree")` (CRAN, NIBIO; GPL).
# Executed with sitree 0.1.15 on R 4.6.2 (Windows) on 2026-11-08; ten seconds.
#     Rscript models/sitree/simulate_stand.R

args   <- commandArgs(trailingOnly = FALSE)
script <- sub("^--file=", "", args[grepl("^--file=", args)])
here   <- if (length(script)) dirname(normalizePath(script)) else file.path(getwd(), "models", "sitree")
root   <- normalizePath(file.path(here, "..", ".."))
source(file.path(here, "sitree_common.R"))

CURVE_NAME    <- "sitree example stand"
PERIOD_LENGTH <- 5
N_PERIODS     <- 50                    # 250 years, the thesis's horizon
SEEDS         <- 2017 + 0:9            # 2017 is the seed the package's own example uses
DEAD_DECAY    <- 0.04                  # share of the dead pool lost per 5-year period (Yatskov et al. 2003)
BARK          <- "without"
OUT           <- file.path(here, "growth_curve_sitree.csv")

tree.df  <- stand.west.tr
stand.df <- stand.west.st
t0 <- Sys.time()
sim <- simulate_replicates(SEEDS, tree.df = tree.df, stand.df = stand.df, n_periods = N_PERIODS,
                           period_length = PERIOD_LENGTH, dead_decay = DEAD_DECAY, bark = BARK)
cat(sprintf("%d replicates in %.0f s\n", length(SEEDS), as.numeric(difftime(Sys.time(), t0, units = "secs"))))

ages_period <- (0:N_PERIODS) * PERIOD_LENGTH
ages        <- 0:(N_PERIODS * PERIOD_LENGTH)
curve <- interpolate_curve(sim, ages_period, ages)

peak_age <- ages_period[which.max(sim$live_mean)]
header <- c(
  sprintf("curve_name: %s", CURVE_NAME),
  sprintf(paste0(
    "SiTree run of the thesis's stand: the package's example stand stand.west.tr / stand.west.st (%d Norway spruce, ",
    "%.0f m2, SI %s, kom %s, the stand Landaas (2023) simulated), run to the thesis's horizon."),
    nrow(tree.df), sum(stand.df$plot.size.m2), unique(stand.df$SI.m), unique(stand.df$kom)),
  provenance_lines(sim, N_PERIODS, PERIOD_LENGTH, SEEDS, BARK, DEAD_DECAY),
  sprintf(paste0(
    "age_years counts from the simulation start, as the thesis's periods do; the stand is already stocked at t0 ",
    "(%.0f m3/ha, %.0f stems/ha, dominant height 13-14 m: about 40 years old on a site index 14 curve), so stand ages ",
    "are that much higher. Mean living volume peaks at %.0f m3/ha at %d simulated years; %.0f m3/ha at 250 years."),
    sim$live_mean[1], sim$stem_mean[1], max(sim$live_mean), peak_age, sim$live_mean[length(sim$live_mean)]),
  sprintf(paste0(
    "Realism check: basal area %.0f m2/ha at t0, %.0f at 50 years, %.0f at 150 years (a real spruce stand rarely exceeds ",
    "70): the 6 400 stems/ha of the example stand lie outside the calibration range of the increment and mortality ",
    "functions, so the absolute volumes are not credible; the relative growth (the shape) is what the rotation model ",
    "compares. See simulate_ssb_stand.R for a stand at a realistic density."),
    sim$ba_mean[1], sim$ba_mean[11], sim$ba_mean[31]),
  sprintf("Written by models/sitree/simulate_stand.R on %s.", format(Sys.Date()))
)
write_curve(OUT, header, curve)
cat("wrote", OUT, "\n")
cat(sprintf("living volume: t0 %.1f, 50 y %.1f, 90 y %.1f, 135 y %.1f, 250 y %.1f m3/ha (sd at 90 y %.1f)\n",
            sim$live_mean[1], sim$live_mean[11], sim$live_mean[19], sim$live_mean[28], sim$live_mean[51], sim$live_sd[19]))
cat(sprintf("dead pool: 90 y %.1f, 250 y %.1f m3/ha; stems/ha t0 %.0f, 250 y %.0f; basal area m2/ha t0 %.0f, 50 y %.0f, 150 y %.0f, 250 y %.0f\n",
            sim$dead_mean[19], sim$dead_mean[51], sim$stem_mean[1], sim$stem_mean[51],
            sim$ba_mean[1], sim$ba_mean[11], sim$ba_mean[31], sim$ba_mean[51]))
