# class_forecast.R -- PLSC 2219 class forecast model, 2026 midterms
#
# Reproduces the class model built in HW4-HW7 (Part 1) and writes the forecast files.
# Run it whenever the national inputs change (presidential approval, gas prices, income);
# each run adds one dated row to the time series. The forecast is locked on November 1.
#
#   cd 2026_assignments/class_model
#   Rscript class_forecast.R          # uses the inputs in the block below
#
# Model (fit separately for the House and the Senate):
#   dem_share = a + b1*pvi + b2*nat_d + b3*inc_d + error
# National vote (HW2 Q2.8 election-year model):
#   change = c0 + c1*midterm + c2*income_1yr + c3*gas_1yr + c4*approval + error
# Uncertainty: one national error shared by every race in both chambers, plus an
# independent district error for each race.

suppressPackageStartupMessages(library(tidyverse))

# ------------------------------------------------------------------ inputs to update
AS_OF            <- as.Date("2026-09-29")  # date these inputs describe
APPROVAL_2026    <- 38.0      # presidential approval, %, average of the major aggregators
GAS_2026         <- 3.8053    # average U.S. regular gas price, Jan 1 to AS_OF (EIA weekly), $
REAL_INCOME_2026 <- NA        # real disposable income per capita, 2026 average so far;
                              # NA keeps the value in the economy file
INPUT_SOURCES    <- "Approval: aggregator average, Sept 18. Gas: EIA weekly, Jan 5 to Sept 21."

# ------------------------------------------------------------------ fixed settings
DATA_DIR    <- "../student_data"   # the course data folder
ECONOMY     <- file.path(DATA_DIR, "economy_sept.csv")
OUT_DIR     <- "output"
TARGET_YEAR <- 2026
N_CYCLES    <- 7        # district model is fit on the last 7 House cycles (2012-2024 for 2026)
N_SIMS      <- 10000
SEED        <- 2219

dir.create(OUT_DIR, showWarnings = FALSE)
set.seed(SEED)

# ------------------------------------------------------------------ 1. national model
elections <- read_csv(file.path(DATA_DIR, "elections_pooled.csv"), show_col_types = FALSE) |>
  mutate(nat_d   = if_else(in_party == "D", in_party_house_share, 100 - in_party_house_share),
         midterm = as.numeric(election_type == "midterm"))

economy <- read_csv(ECONOMY, show_col_types = FALSE) |> arrange(year)
economy$approval[economy$year == TARGET_YEAR]  <- APPROVAL_2026
economy$gas_price[economy$year == TARGET_YEAR] <- GAS_2026
if (!is.na(REAL_INCOME_2026)) economy$real_disp_income[economy$year == TARGET_YEAR] <- REAL_INCOME_2026
economy <- economy |>
  mutate(income_1yr = 100 * (real_disp_income - lag(real_disp_income)) / lag(real_disp_income),
         gas_1yr    = 100 * (gas_price - lag(gas_price)) / lag(gas_price))

national_data <- elections |>
  filter(year != 1954, year < TARGET_YEAR) |>
  left_join(select(economy, year, income_1yr, gas_1yr), by = "year")
m_nat <- lm(change ~ midterm + income_1yr + gas_1yr + approval, data = national_data)

new_nat  <- economy |> filter(year == TARGET_YEAR) |>
  mutate(midterm = 1) |> select(midterm, income_1yr, gas_1yr, approval)
nat_pred <- predict(m_nat, newdata = new_nat, se.fit = TRUE)
pred_change <- unname(nat_pred$fit)
sigma_nat   <- unname(sqrt(nat_pred$se.fit^2 + summary(m_nat)$sigma^2))

# 2026: the president's party is the Republicans; Democrats held the White House in 2024
prev <- elections |> filter(year == max(year))
rep_prev <- if (prev$in_party == "R") prev$in_party_house_share else 100 - prev$in_party_house_share
NAT_D <- 100 - (rep_prev + pred_change)

# ------------------------------------------------------------------ 2. district models
results <- read_csv(file.path(DATA_DIR, "hw4_results_2012_2024.csv"), show_col_types = FALSE) |>
  filter(contested == 1) |>
  left_join(select(elections, year, nat_d), by = "year") |>
  mutate(inc_d = case_when(incumbent_party == "D" ~ 1, incumbent_party == "R" ~ -1, TRUE ~ 0))

cycles <- sort(unique(results$year[results$office == "house" & results$year < TARGET_YEAR]),
               decreasing = TRUE)[1:N_CYCLES]
fit_data <- results |> filter(year %in% cycles)

m_house  <- lm(dem_share ~ pvi + nat_d + inc_d, data = filter(fit_data, office == "house"))
m_senate <- lm(dem_share ~ pvi + nat_d + inc_d, data = filter(fit_data, office == "senate"))

# ------------------------------------------------------------------ 3. predictions
races <- read_csv(file.path(DATA_DIR, "races_2026.csv"), show_col_types = FALSE) |>
  mutate(nat_d = NAT_D,
         inc_d = case_when(open_seat == 1         ~  0,
                           incumbent_party == "D" ~  1,
                           incumbent_party == "R" ~ -1,
                           r_side_party == "I"    ~ -1,
                           TRUE                   ~  0))

err <- function(m) {
  sd_nat_race <- unname(coef(m)["nat_d"]) * sigma_nat
  c(nat_race = sd_nat_race, dist = sigma(m), total = sqrt(sd_nat_race^2 + sigma(m)^2))
}
e_h <- err(m_house); e_s <- err(m_senate)

races <- races |>
  mutate(pred  = if_else(office == "house",
                         predict(m_house, newdata = races), predict(m_senate, newdata = races)),
         sd    = if_else(office == "house", e_h["total"], e_s["total"]),
         fixed = !is.na(fixed_prediction),
         pred  = if_else(fixed, 100 * fixed_prediction, pred),
         p_dem_win = if_else(fixed, fixed_prediction, 1 - pnorm(50, pred, sd)),
         lo_80 = if_else(fixed, pred, pmax(0,   pred - qnorm(0.9) * sd)),
         hi_80 = if_else(fixed, pred, pmin(100, pred + qnorm(0.9) * sd)))

# ------------------------------------------------------------------ 4. simulations
# One national draw per simulation (standard units), shared by BOTH chambers.
national_draws <- rnorm(N_SIMS)
simulate_seats <- function(pred, sd_nat_race, sd_dist, seats_not_up = 0) {
  sapply(seq_len(N_SIMS), function(i)
    seats_not_up + sum(pred + national_draws[i] * sd_nat_race +
                       rnorm(length(pred), 0, sd_dist) > 50))
}
not_up <- read_csv(file.path(DATA_DIR, "senate_seats_not_up_2026.csv"), show_col_types = FALSE)
dem_not_up <- not_up$seats_not_up[not_up$party != "R"]

h <- filter(races, office == "house"); s <- filter(races, office == "senate")
house_sims  <- simulate_seats(h$pred, e_h["nat_race"], e_h["dist"])
senate_sims <- simulate_seats(s$pred, e_s["nat_race"], e_s["dist"], seats_not_up = dem_not_up)

# ------------------------------------------------------------------ 5. write outputs
stamp <- format(AS_OF, "%Y-%m-%d")
forecast <- races |>
  transmute(race_id, office, state, district, dem_candidate, rep_candidate, race_type, scored,
            pred_dem_share = pred / 100, p_dem_win, lo_80 = lo_80 / 100, hi_80 = hi_80 / 100)
sims <- tibble(sim = seq_len(N_SIMS), house_dem_seats = house_sims, senate_dem_seats = senate_sims)

write_csv(forecast, file.path(OUT_DIR, paste0("forecast_class_", stamp, ".csv")))
write_csv(sims,     file.path(OUT_DIR, paste0("seat_sims_class_", stamp, ".csv")))
write_csv(forecast, file.path(OUT_DIR, "forecast_class_latest.csv"))
write_csv(sims,     file.path(OUT_DIR, "seat_sims_class_latest.csv"))

summary_row <- tibble(
  as_of = stamp, approval = APPROVAL_2026, gas_ytd = GAS_2026,
  income_1yr = new_nat$income_1yr, gas_1yr = new_nat$gas_1yr,
  pred_change_in_party = pred_change, nat_dem_share = NAT_D, sigma_nat = sigma_nat,
  house_seats_point = sum(h$pred > 50), house_seats_expected = sum(h$p_dem_win),
  house_seats_median = median(house_sims),
  house_lo80 = unname(quantile(house_sims, 0.1)), house_hi80 = unname(quantile(house_sims, 0.9)),
  p_house = mean(house_sims >= 218),
  senate_seats_median = median(senate_sims),
  senate_lo80 = unname(quantile(senate_sims, 0.1)), senate_hi80 = unname(quantile(senate_sims, 0.9)),
  p_senate = mean(senate_sims >= 51),
  p_both = mean(house_sims >= 218 & senate_sims >= 51),
  fit_cycles = paste(range(cycles), collapse = "-"), sources = INPUT_SOURCES)

ts_file <- file.path(OUT_DIR, "class_timeseries.csv")
ts <- if (file.exists(ts_file)) read_csv(ts_file, show_col_types = FALSE,
                                         col_types = cols(as_of = col_character())) else NULL
ts <- bind_rows(filter(ts %||% summary_row[0, ], as_of != stamp), summary_row) |> arrange(as_of)
write_csv(ts, ts_file)

print(t(summary_row))
