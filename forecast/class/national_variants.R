# national_variants.R -- other versions of the class model's national equation
#
#   Rscript forecast/class/national_variants.R [output_dir]
#
# The class model (class_forecast.R) predicts the change in the president's
# party's House vote share from midterm, income_1yr, gas_1yr and approval.
# This script fits that equation and three alternatives on the same 35
# elections and applies each to the inputs of every date in class_inputs.csv:
#
#   course            midterm + income_1yr + gas_1yr + approval   (the class model)
#   sentiment         midterm + sentiment  + gas_1yr + approval   (sentiment level replaces income)
#   sentiment_change  midterm + sentiment_1yr + gas_1yr + approval
#   approval_only     midterm + approval
#
# Historical economy terms are calendar-year averages from economy_sept.csv.
# For a forecast date, sentiment is the average of the latest 12 released
# months of FRED UMCSENT and sentiment_1yr its change from the 12 months
# before; income_1yr and gas_1yr are the one-year changes the runner records.
#
# Outputs (in output_dir):
#   class_national_variants.csv      one row per date and version
#   class_national_variants_fit.csv  coefficients and fit statistics
#
# National vote only. Seats and chances of control come from the full class
# model, which uses the course equation.

suppressPackageStartupMessages(library(tidyverse))

args    <- commandArgs(trailingOnly = TRUE)
here    <- local({
  f <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))
  if (length(f)) dirname(normalizePath(f)) else "forecast/class"
})
OUT_DIR <- if (length(args)) args[1] else file.path(here, "output")
IN_DIR  <- file.path(here, "inputs")

elections <- read_csv(file.path(IN_DIR, "elections_pooled.csv"), show_col_types = FALSE) |>
  mutate(midterm = as.numeric(election_type == "midterm"))
economy <- read_csv(file.path(IN_DIR, "economy_sept.csv"), show_col_types = FALSE) |>
  arrange(year) |>
  mutate(income_1yr    = 100 * (real_disp_income / lag(real_disp_income) - 1),
         gas_1yr       = 100 * (gas_price / lag(gas_price) - 1),
         sentiment     = consumer_sentiment,
         sentiment_1yr = consumer_sentiment - lag(consumer_sentiment))
hist <- elections |>
  filter(year != 1954, year < 2026) |>
  left_join(select(economy, year, income_1yr, gas_1yr, sentiment, sentiment_1yr), by = "year")

specs <- list(
  course           = change ~ midterm + income_1yr + gas_1yr + approval,
  sentiment        = change ~ midterm + sentiment + gas_1yr + approval,
  sentiment_change = change ~ midterm + sentiment_1yr + gas_1yr + approval,
  approval_only    = change ~ midterm + approval)
models <- map(specs, lm, data = hist)

prev     <- elections |> filter(year == max(year))
rep_prev <- if (prev$in_party == "R") prev$in_party_house_share else 100 - prev$in_party_house_share

# ---- fit statistics
fit <- imap_dfr(models, function(m, k) {
  s  <- summary(m)
  cf <- coef(s)
  loo <- sqrt(mean((resid(m) / (1 - hatvalues(m)))^2))   # leave-one-out error
  tibble(version = k, term = rownames(cf), estimate = cf[, 1], std_error = cf[, 2],
         t = cf[, 3], n = nobs(m), r2 = s$r.squared, adj_r2 = s$adj.r.squared,
         resid_se = s$sigma, loo_rmse = loo, first_year = min(hist$year),
         last_year = max(hist$year))
})
write_csv(fit, file.path(OUT_DIR, "class_national_variants_fit.csv"))

# ---- every date
inputs <- read_csv(file.path(OUT_DIR, "class_inputs.csv"), show_col_types = FALSE,
                   col_types = cols(.default = col_character())) |>
  transmute(as_of = date, midterm = 1,
            approval = as.numeric(approval),
            income_1yr = as.numeric(income_1yr), gas_1yr = as.numeric(gas_1yr),
            sentiment = suppressWarnings(as.numeric(sentiment_12m)),
            sentiment_1yr = sentiment - suppressWarnings(as.numeric(sentiment_prev_12m)))

rows <- imap_dfr(models, function(m, k) {
  vars <- all.vars(formula(m))[-1]
  ok   <- complete.cases(inputs[, vars])
  if (!any(ok)) return(tibble())
  nd   <- inputs[ok, ]
  p    <- predict(m, newdata = nd, se.fit = TRUE)
  sig  <- sqrt(p$se.fit^2 + summary(m)$sigma^2)
  nat  <- 100 - (rep_prev + p$fit)
  tibble(as_of = nd$as_of, version = k, pred_change_in_party = unname(p$fit),
         nat_dem_share = unname(nat), sigma_nat = unname(sig),
         p_dem_majority_vote = unname(1 - pnorm(50, nat, sig)))
}) |> arrange(as_of, version)
write_csv(rows, file.path(OUT_DIR, "class_national_variants.csv"))

# ---- check: the "course" version must reproduce the class model's own numbers
ts_path <- file.path(OUT_DIR, "class_timeseries.csv")
if (file.exists(ts_path)) {
  ts <- read_csv(ts_path, show_col_types = FALSE,
                 col_types = cols(.default = col_character())) |>
    transmute(as_of, nat_class = as.numeric(nat_dem_share))
  chk <- rows |> filter(version == "course") |> inner_join(ts, by = "as_of")
  gap <- if (nrow(chk)) max(abs(chk$nat_dem_share - chk$nat_class)) else NA
  cat(sprintf("  course version vs class model: %d dates, largest gap %.4f points\n",
              nrow(chk), gap))
}
latest <- rows |> filter(as_of == max(as_of))
for (i in seq_len(nrow(latest)))
  cat(sprintf("  %-17s %s  national D %.2f%%\n", latest$version[i], latest$as_of[i],
              latest$nat_dem_share[i]))
