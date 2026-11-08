# Shared helpers for the SiTree runs in this folder (sourced by simulate_stand.R
# and simulate_ssb_stand.R). Nothing here is specific to one stand.
#
# Conventions: dbh in mm and height in dm, as SiTree and the NFI volume
# functions expect; volumes in m3 per hectare; one row per 5-year period until
# `interpolate_curve` puts the result on the annual grid the rotation model
# reads (models/rotation/rotation.py, GrowthCurve.from_csv).

suppressPackageStartupMessages(library(sitree))

`%||%` <- function(a, b) if (is.null(a)) b else a

#' Stem volume (m3) of every tree alive in a period; 0 otherwise.
#'
#' After a run SiTree returns every tree that was ever alive, with dbh and
#' height 0 (not NA) in the periods before recruitment and after death.
#' `volume.norway` is the national forest inventory's single-tree function; its
#' domain starts at 50 mm, so a smaller recruit is valued at 50 mm.
tree_volume_m3 <- function(dbh_mm, height_dm, tree_sp, kom, bark = "without") {
  alive <- !is.na(dbh_mm) & !is.na(height_dm) & dbh_mm > 0 & height_dm > 0
  out <- numeric(length(dbh_mm))
  attr(out, "clamped") <- 0L
  attr(out, "no_volume") <- 0L
  if (any(alive)) {
    small <- alive & dbh_mm < 50
    dbh_mm[small] <- 50
    v <- volume.norway(dbh.mm = dbh_mm[alive], height.dm = height_dm[alive],
                       tree.sp = tree_sp[alive], kom = kom[alive])
    vol <- if (bark == "without") v$vol.wo.tr.m3 else v$vol.w.tr.m3
    attr(out, "no_volume") <- sum(is.na(vol))
    vol[is.na(vol)] <- 0
    out[alive] <- vol
    attr(out, "clamped") <- sum(small)
  }
  out
}

#' Grow one stand once, without management, and return per-period stand figures per hectare.
#'
#' The call is the one the package documents for its example stand: diameter
#' increment of Bollandsås & Næsset (2009) with Korf height increment through
#' `grow.dbhinc.hgtinc`, mortality of Bollandsås (2007), recruitment of
#' Bollandsås, Buongiorno & Gobakken (2008). Dead trees enter a pool at their
#' volume at death (`last.measurement`) and the pool loses `dead_decay` per period.
simulate_once <- function(seed, tree.df, stand.df, n_periods, period_length = 5,
                          dead_decay = 0.04, bark = "without") {
  set.seed(seed)
  res <- sitree(
    tree.df   = tree.df,
    stand.df  = stand.df,
    functions = list(
      fn.growth           = "grow.dbhinc.hgtinc",
      fn.mort             = "mort.B2007",
      fn.recr             = "recr.BBG2008",
      fn.management       = NULL,
      fn.tree.removal     = NULL,
      fn.modif            = NULL,
      fn.prep.common.vars = "prep.common.vars.fun"
    ),
    n.periods      = n_periods,
    period.length  = period_length,
    mng.options    = NA,
    print.comments = FALSE,
    fn.dbh.inc     = "dbhi.BN2009",
    fn.hgt.inc     = "height.korf"
  )
  tree2ha <- 1e4 / sum(stand.df$plot.size.m2)
  kom_of_plot <- setNames(stand.df$kom, stand.df$plot.id)
  periods <- paste0("t", 0:n_periods)
  live <- res$live$data
  kom_live <- kom_of_plot[as.character(live$plot.id)]
  clamped <- 0L
  no_volume <- 0L
  live_m3_ha <- vapply(periods, function(p) {
    v <- tree_volume_m3(live$dbh.mm[[p]], live$height.dm[[p]], live$tree.sp, kom_live, bark)
    clamped <<- clamped + attr(v, "clamped")
    no_volume <<- no_volume + attr(v, "no_volume")
    sum(v) * tree2ha
  }, numeric(1))
  stems_ha <- vapply(periods, function(p) sum(live$dbh.mm[[p]] > 0, na.rm = TRUE) * tree2ha, numeric(1))
  ba_ha <- vapply(periods, function(p) {
    d <- live$dbh.mm[[p]]
    sum(pi * (d[!is.na(d) & d > 0] / 2000)^2) * tree2ha
  }, numeric(1))
  lm   <- res$dead$last.measurement
  dead <- res$dead$data
  idx  <- match(lm$treeid, dead$treeid)
  v_dead <- tree_volume_m3(lm$dbh.mm, lm$height.dm, dead$tree.sp[idx],
                           kom_of_plot[as.character(dead$plot.id[idx])], bark)
  dead_in <- vapply(periods, function(p) sum(v_dead[as.character(lm$found.dead) == p]) * tree2ha, numeric(1))
  pool <- numeric(length(periods))
  for (i in seq_along(periods)) {
    pool[i] <- (if (i == 1) 0 else pool[i - 1] * (1 - dead_decay)) + dead_in[i]
  }
  list(live = live_m3_ha, dead = pool, dead_in = dead_in, stems = stems_ha, ba = ba_ha,
       clamped = clamped, no_volume = no_volume, n_dead = nrow(lm),
       n_recruits = length(live$treeid) - nrow(tree.df))
}

#' Run `simulate_once` for every seed and average the per-period series.
simulate_replicates <- function(seeds, ...) {
  runs <- lapply(seeds, simulate_once, ...)
  pick <- function(what) sapply(runs, `[[`, what)
  live_mat <- pick("live")
  list(
    runs      = runs,
    live_mean = rowMeans(live_mat),
    live_sd   = apply(live_mat, 1, sd),
    dead_mean = rowMeans(pick("dead")),
    dead_in_mean = rowMeans(pick("dead_in")),
    stem_mean = rowMeans(pick("stems")),
    ba_mean   = rowMeans(pick("ba")),
    clamped   = sum(sapply(runs, `[[`, "clamped")),
    no_volume = sum(sapply(runs, `[[`, "no_volume")),
    tree_periods = round(sum(pick("stems")) / (1e4 / sum(list(...)$stand.df$plot.size.m2))),
    n_dead    = mean(sapply(runs, `[[`, "n_dead")),
    n_recruits = mean(sapply(runs, `[[`, "n_recruits"))
  )
}

#' Linear interpolation of the 5-year series to the annual grid the rotation model reads.
interpolate_curve <- function(sim, ages_period, ages) {
  at <- function(x, digits) round(approx(ages_period, x, xout = ages)$y, digits)
  data.frame(
    age_years         = ages,
    volume_m3_ha      = at(sim$live_mean, 4),
    dead_volume_m3_ha = at(sim$dead_mean, 4),
    volume_sd_m3_ha   = at(sim$live_sd, 4),
    stems_per_ha      = at(sim$stem_mean, 1),
    basal_area_m2_ha  = at(sim$ba_mean, 2)
  )
}

#' Write `#` header lines and the curve with LF line endings, like every committed CSV.
write_curve <- function(path, header, curve) {
  con <- file(path, open = "wb")
  writeLines(paste0("# ", header), con, sep = "\n")
  write.table(curve, con, sep = ",", row.names = FALSE, col.names = TRUE, quote = FALSE, eol = "\n")
  close(con)
  invisible(path)
}

#' The lines every export's header carries about the run itself.
provenance_lines <- function(sim, n_periods, period_length, seeds, bark, dead_decay) {
  c(
    sprintf(paste0(
      "Functions: grow.dbhinc.hgtinc (dbhi.BN2009, height.korf), mort.B2007, recr.BBG2008, prep.common.vars.fun - the call ",
      "?sitree documents - %d x %d years without management; sitree %s on R %s."),
      n_periods, period_length, as.character(packageVersion("sitree")), paste(R.version$major, R.version$minor, sep = ".")),
    sprintf(paste0(
      "volume_m3_ha: volume.norway stem volume %s bark of every tree alive, per ha; dead_volume_m3_ha: pool of trees found ",
      "dead at their volume at death, losing %.0f %% per period. Mean of %d replicates (seeds %d-%d; mortality and ",
      "recruitment are stochastic), linearly interpolated from 5-year periods to years; volume_sd_m3_ha is the spread ",
      "across replicates; stems_per_ha and basal_area_m2_ha are informative."),
      bark, 100 * dead_decay, length(seeds), min(seeds), max(seeds)),
    sprintf("%d trees died and %d were recruited per replicate on average; %d tree-period volumes used dbh 50 mm for a smaller recruit and %d got no volume from volume.norway (counted as 0).",
            round(sim$n_dead), round(sim$n_recruits), sim$clamped, sim$no_volume)
  )
}
