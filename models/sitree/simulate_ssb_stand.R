# Simulate a representative Norwegian spruce stand with SiTree, anchored in
# Statistics Norway's forest inventory tables — the realistic alternative to the
# package's example stand (simulate_stand.R).
#
# What SSB can and cannot give. The national forest inventory tables the
# pipeline already ingests (data/processed/forest_resource.csv, Phase H) publish
# *areas by site class* (14594, H40 classes 6–26), *areas by development class*
# (14594, classes I–V), and *standing volume and gross increment by species*
# (14589, 14590, 06289) on productive forestry land, plus forest-type areas
# (14593). They publish no tree lists and no stand tables, so a SiTree stand
# cannot be read from SSB; it can be *anchored* in SSB:
#   1. the site index comes from the site-class area distribution — the three
#      classes H40 11, 14 and 17 hold about half of productive forestry land and
#      most of the spruce (SSB does not split site class by species), and SI 14
#      is the representative stand, 11 and 17 its neighbours;
#   2. the simulated curve is validated against SSB's landscape means: the
#      development-class areas weight the curve into an implied mean standing
#      volume and gross increment per hectare, compared with SSB's observed
#      spruce volume and increment per hectare of spruce forest.
# What is *not* SSB and is an assumption, stated here and in the header:
#   - the planting density (2 000 stems/ha, within the Norwegian regeneration
#     norms of 180–250 plants per decare) and the start as a uniform young
#     stand at the functions' 50 mm floor (dbh 5–8 cm, 4–5.5 m tall);
#   - the total age at that start (25 / 20 / 15 years for SI 11 / 14 / 17: the
#     years to about 5 m dominant height on an H40 height curve, approximate);
#   - the municipality (3420, Innlandet, where most spruce grows; the volume
#     functions only distinguish western Norway) and the latitude (60.9).
#
# Functions, volume and dead pool as in sitree_common.R (the call the package
# documents; `volume.norway` stem volume without bark; ten replicates averaged).
# `age_years` is the stand's total age, so the rotation model reads it unshifted.
#
# Requirements: R >= 4.1 and `install.packages("sitree")` (CRAN, NIBIO; GPL).
# Executed with sitree 0.1.15 on R 4.6.2 (Windows) on 2026-11-08; about 30 s.
#     Rscript models/sitree/simulate_ssb_stand.R
# Writes models/sitree/growth_curve_ssb_si{11,14,17}.csv and
# models/sitree/ssb_validation.csv.

args   <- commandArgs(trailingOnly = FALSE)
script <- sub("^--file=", "", args[grepl("^--file=", args)])
here   <- if (length(script)) dirname(normalizePath(script)) else file.path(getwd(), "models", "sitree")
root   <- normalizePath(file.path(here, "..", ".."))
source(file.path(here, "sitree_common.R"))

SI_RUNS           <- c(11, 14, 17)
REPRESENTATIVE_SI <- 14
START_AGE         <- c(`11` = 25, `14` = 20, `17` = 15)   # total age at the simulation start (assumption, see above)
STEMS_PER_HA      <- 2000                                 # Norwegian regeneration norms 180-250 plants/daa (assumption)
PLOT_M2           <- 1000
KOM               <- 3420                                 # Elverum, Innlandet (volume functions only distinguish the west)
LAT_DEG           <- 60.9
HORIZON           <- 250                                  # the thesis's horizon, as the other curves
PERIOD_LENGTH     <- 5
SEEDS             <- 2017 + 0:9
DEAD_DECAY        <- 0.04
BARK              <- "without"
MATURE_MAX_AGE    <- 140                                  # upper age of development class V in the validation weights (assumption)

# ---------------------------------------------------------------- SSB anchors (national, latest interval)
forest <- read.csv(file.path(root, "data", "processed", "forest_resource.csv"), encoding = "UTF-8",
                   colClasses = c(table = "character", interval = "character", region = "character"))
nat <- forest[forest$region == "NO", ]
latest <- function(tab) { g <- nat[nat$table == tab, ]; g[g$year_end == max(g$year_end), ] }
t14594 <- latest("14594"); t14589 <- latest("14589"); t14590 <- latest("14590"); t14593 <- latest("14593")
val <- function(df, measure, species = "all") {
  v <- df$value[df$measure == measure & df$species == species]
  stopifnot(length(v) == 1); v
}
site_codes  <- c(`6` = "Bonitet9", `8` = "Bonitet8", `11` = "Bonitet6", `14` = "Bonitet5", `17` = "Bonitet4", `20` = "Bonitet3", `23` = "Bonitet2")
site_share  <- sapply(site_codes, function(m) val(t14594, m) / val(t14594, "BonitetTot"))
class_share <- c(`1-2` = (val(t14594, "HogstI") + val(t14594, "HogstII")) / val(t14594, "HogstTot"),
                 `3` = val(t14594, "HogstIII") / val(t14594, "HogstTot"),
                 `4` = val(t14594, "HogstIV") / val(t14594, "HogstTot"),
                 `5` = val(t14594, "HogstV") / val(t14594, "HogstTot"))
spruce_vol_m3  <- val(t14589, "Skogsareal4", "spruce") * 1e3          # standing volume, m3
spruce_incr_m3 <- val(t14590, "Skogsareal4", "spruce") * 1e3          # gross increment, m3/yr
area_spruce_ha <- val(t14593, "Granskog") * 100                       # km2 -> ha
area_conifer_ha <- area_spruce_ha + (val(t14593, "Bardominert") + val(t14593, "Blanding")) * 100
ssb_vol_band  <- c(low = spruce_vol_m3 / area_conifer_ha,  high = spruce_vol_m3 / area_spruce_ha)   # m3/ha
ssb_incr_band <- c(low = spruce_incr_m3 / area_conifer_ha, high = spruce_incr_m3 / area_spruce_ha)  # m3/ha/yr
interval_label <- unique(t14594$interval)
cat(sprintf("SSB %s: site-class shares %s; development-class shares %s\n", interval_label,
            paste(sprintf("H40 %s %.0f%%", names(site_share), 100 * site_share), collapse = ", "),
            paste(sprintf("%s %.0f%%", names(class_share), 100 * class_share), collapse = ", ")))
cat(sprintf("SSB spruce: %.0f-%.0f m3/ha standing, %.1f-%.1f m3/ha/yr gross increment (volume over spruce forest alone, high; over spruce plus conifer-dominated and mixed forest, low)\n",
            ssb_vol_band["low"], ssb_vol_band["high"], ssb_incr_band["low"], ssb_incr_band["high"]))

# ---------------------------------------------------------------- the stand
make_stand <- function(si, seed = 1) {
  set.seed(seed)
  n <- round(STEMS_PER_HA * PLOT_M2 / 1e4)
  dbh <- pmax(50, round(rnorm(n, mean = 62, sd = 10)))                 # mm; the functions' domain starts at 50 mm
  height <- round(10 * (4.0 + 0.5 * (dbh / 10 - 5)))                   # dm; 4 m at 5 cm, 5.5 m at 8 cm
  tree.df <- data.frame(plot.id = 1, treeid = seq_len(n), dbh = dbh, height = height,
                        tree.sp = factor("1", levels = levels(stand.west.tr$tree.sp)))
  stand.df <- data.frame(plot.id = 1L, SI.m = si, SI.spp = 1, plot.size.m2 = PLOT_M2, tree2ha = 1e4 / PLOT_M2,
                         lat.deg = LAT_DEG, subplot.size.m2 = PLOT_M2, kom = KOM)
  list(tree.df = tree.df, stand.df = stand.df)
}

validation <- list()
for (si in SI_RUNS) {
  start <- START_AGE[[as.character(si)]]
  n_periods <- (HORIZON - start) %/% PERIOD_LENGTH
  stand <- make_stand(si)
  t0 <- Sys.time()
  sim <- simulate_replicates(SEEDS, tree.df = stand$tree.df, stand.df = stand$stand.df, n_periods = n_periods,
                             period_length = PERIOD_LENGTH, dead_decay = DEAD_DECAY, bark = BARK)
  ages_period <- start + (0:n_periods) * PERIOD_LENGTH
  ages <- start:(start + n_periods * PERIOD_LENGTH)
  curve <- interpolate_curve(sim, ages_period, ages)

  # Validation against SSB: weight the curve by the development-class area shares.
  classes <- calculate.development.class(SI.spp = 1, SI.m = si, stand.age.years = ages)
  windows <- list(`1-2` = ages[classes == 1], `3` = ages[classes == 3], `4` = ages[classes == 4],
                  `5` = ages[classes == 5 & ages <= MATURE_MAX_AGE])
  gross_rate <- approx(ages_period, sim$dead_in_mean / PERIOD_LENGTH, xout = ages)$y          # dead inflow per year
  net_growth <- c(diff(curve$volume_m3_ha), NA)
  gross_incr <- net_growth + gross_rate
  mean_in <- function(x, w) mean(x[match(w, ages)], na.rm = TRUE)
  implied_vol  <- sum(class_share * sapply(windows, function(w) mean_in(curve$volume_m3_ha, w)))
  implied_incr <- sum(class_share * sapply(windows, function(w) mean_in(gross_incr, w)))
  bounds <- sapply(windows, range)
  peak_i <- which.max(sim$live_mean)
  validation[[length(validation) + 1]] <- data.frame(
    si = si, start_age_years = start, stems_per_ha_start = STEMS_PER_HA,
    class_windows_years = paste(sprintf("%s:%d-%d", names(windows), bounds[1, ], bounds[2, ]), collapse = " "),
    implied_volume_m3_ha = round(implied_vol, 1), ssb_volume_low = round(ssb_vol_band["low"], 1), ssb_volume_high = round(ssb_vol_band["high"], 1),
    implied_gross_increment_m3_ha_yr = round(implied_incr, 2), ssb_increment_low = round(ssb_incr_band["low"], 2), ssb_increment_high = round(ssb_incr_band["high"], 2),
    peak_volume_m3_ha = round(max(sim$live_mean), 1), peak_age_years = ages_period[peak_i],
    volume_at_100_m3_ha = round(curve$volume_m3_ha[match(100, ages)], 1),
    max_basal_area_m2_ha = round(max(sim$ba_mean), 1), stems_per_ha_at_100 = round(approx(ages_period, sim$stem_mean, xout = 100)$y),
    site_class_share = round(site_share[[as.character(si)]], 3), row.names = NULL
  )
  cat(sprintf("SI %d: %d replicates in %.0f s; peak %.0f m3/ha at %d y; at 100 y %.0f m3/ha, %.0f stems/ha, BA max %.0f; implied landscape %.0f m3/ha (SSB %.0f-%.0f), gross increment %.1f (SSB %.1f-%.1f)\n",
              si, length(SEEDS), as.numeric(difftime(Sys.time(), t0, units = "secs")), max(sim$live_mean), ages_period[peak_i],
              curve$volume_m3_ha[match(100, ages)], approx(ages_period, sim$stem_mean, xout = 100)$y, max(sim$ba_mean),
              implied_vol, ssb_vol_band["low"], ssb_vol_band["high"], implied_incr, ssb_incr_band["low"], ssb_incr_band["high"]))

  header <- c(
    sprintf("curve_name: ssb-anchored spruce stand SI %d", si),
    sprintf(paste0(
      "A representative Norway-spruce stand anchored in Statistics Norway's forest inventory tables (data/processed/",
      "forest_resource.csv, interval %s): site index H40 %d holds %.0f %% of productive forestry land (H40 11/14/17 together ",
      "%.0f %%; SSB does not split site class by species); %d stems/ha planted (Norwegian regeneration norms 180-250 per ",
      "decare - an assumption, not SSB), started at total age %d as a uniform young stand (dbh 5-8 cm, 4-5.5 m; the years ",
      "to about 5 m dominant height on an H40 curve, approximate), kom %d, latitude %.1f."),
      interval_label, si, 100 * site_share[[as.character(si)]], 100 * sum(site_share[c("11", "14", "17")]),
      STEMS_PER_HA, start, KOM, LAT_DEG),
    provenance_lines(sim, n_periods, PERIOD_LENGTH, SEEDS, BARK, DEAD_DECAY),
    sprintf(paste0(
      "Validation against SSB: weighting the curve by the development-class area shares (I+II %.0f %%, III %.0f %%, IV %.0f %%, ",
      "V %.0f %%; class V capped at %d years) gives an implied landscape mean of %.0f m3/ha standing and %.1f m3/ha/yr gross ",
      "increment, against SSB's spruce %.0f-%.0f m3/ha and %.1f-%.1f m3/ha/yr (volume over spruce forest alone = high; over ",
      "spruce plus conifer-dominated and mixed forest = low). Details in ssb_validation.csv."),
      100 * class_share[["1-2"]], 100 * class_share[["3"]], 100 * class_share[["4"]], 100 * class_share[["5"]], MATURE_MAX_AGE,
      implied_vol, implied_incr, ssb_vol_band["low"], ssb_vol_band["high"], ssb_incr_band["low"], ssb_incr_band["high"]),
    sprintf(paste0(
      "Realism check: basal area peaks at %.0f m2/ha; %.0f stems/ha and %.0f m3/ha at 100 years; mean living volume peaks at ",
      "%.0f m3/ha at %d years. age_years is the stand's total age (no offset)."),
      max(sim$ba_mean), approx(ages_period, sim$stem_mean, xout = 100)$y, curve$volume_m3_ha[match(100, ages)],
      max(sim$live_mean), ages_period[peak_i]),
    sprintf("Written by models/sitree/simulate_ssb_stand.R on %s.", format(Sys.Date()))
  )
  write_curve(file.path(here, sprintf("growth_curve_ssb_si%d.csv", si)), header, curve)
}

val_df <- do.call(rbind, validation)
val_path <- file.path(here, "ssb_validation.csv")
con <- file(val_path, open = "wb")
writeLines(paste0("# ", c(
  sprintf("SSB-anchored SiTree stands against Statistics Norway's landscape means (forest_resource.csv, interval %s); written by models/sitree/simulate_ssb_stand.R on %s.", interval_label, format(Sys.Date())),
  "implied_*: the simulated curve weighted by SSB's development-class area shares (class windows from sitree::calculate.development.class, class V capped at MATURE_MAX_AGE); ssb_*: spruce standing volume (14589) and gross increment (14590) per ha of spruce forest (high) or of spruce plus conifer-dominated and mixed forest (14593; low).",
  "The representative stand is SI 14; 11 and 17 are its neighbours. Stems per ha and start ages are assumptions, not SSB figures (see the script)."
)), con, sep = "\n")
write.table(val_df, con, sep = ",", row.names = FALSE, col.names = TRUE, quote = TRUE, eol = "\n")
close(con)
cat("wrote", val_path, "\n")
