import random

SPIN_SHOT_MATRIX = {
    "Off spin": ["Sweep", "Drive", "Flick"],
    "Carrom": ["Cut", "Drive", "Loft"],
    "Arm ball": ["Loft", "Drive", "Block"],
    "Doosra": ["Cut", "Sweep", "Drive"],
    "Top spin": ["Cut", "Drive", "Pull"],
    "Leg spin": ["Cut", "Drive", "Loft", "Sweep"],
    "Googly": ["Pull", "Drive", "Sweep"],
    "Flipper": ["Drive", "Flick", "Block"],
    "Drifter": ["Loft", "Drive", "Cut"],
    "Slider": ["Flick", "Drive", "Sweep"],
    "Mystery": ["Block", "Sweep", "Drive"] 
}

def get_smart_ai_shot_t20(deliv, is_collapse, is_death_overs, archetype):
    if is_collapse:
        return random.choices(["Block", "Drive", "Flick", "Cut"], weights=[40, 30, 15, 15], k=1)[0]
        
    if is_death_overs:
        if archetype == "Anchor":
            return random.choices(["Drive", "Loft", "Pull", "Flick"], weights=[30, 30, 20, 20], k=1)[0]
        elif archetype == "Standard":
            return random.choices(["Loft", "Drive", "Pull", "Flick"], weights=[30, 30, 20, 20], k=1)[0]
        else:
            return random.choices(["Loft", "Pull", "Scoop", "Sweep"], weights=[40, 25, 15, 20], k=1)[0]
            
    if "Yorker" in deliv:
        return random.choices(["Block", "Drive", "Flick"], weights=[40, 40, 20], k=1)[0]
    elif "Bouncer" in deliv:
        return random.choices(["Pull", "Cut", "Block"], weights=[50, 40, 10], k=1)[0]
    elif "Full" in deliv:
        return random.choices(["Drive", "Loft", "Flick"], weights=[40, 40, 20], k=1)[0]
    elif deliv in SPIN_SHOT_MATRIX:
        if random.random() < 0.7:
            return random.choice(SPIN_SHOT_MATRIX[deliv])
        else:
            return random.choices(["Drive", "Sweep", "Cut", "Block"], weights=[30, 30, 20, 20], k=1)[0]
    else:
        return random.choices(["Drive", "Cut", "Flick", "Block"], weights=[35, 25, 25, 15], k=1)[0]

def get_smart_ai_bowler_t20(innings, pitch, weather="Clear", format_overs=20):
    valid_bowlers = []
    bowler_quota = max(1, (format_overs + 4) // 5)
    
    for p in innings.bowling_team["players"]:
        if ("Bowler" in p["role"] or "All-Rounder" in p["role"]):
            stats = innings.bowling_stats[p["name"]]
            if (stats.balls_bowled // 6) < bowler_quota:
                # Make sure they aren't bowling two overs in a row
                if not innings.current_bowler or innings.current_bowler["name"] != p["name"]:
                    valid_bowlers.append(p)
                    
    if not valid_bowlers:
        return None

    current_over = innings.total_balls // 6
    weights = []
    
    for p in valid_bowlers:
        stats = innings.bowling_stats[p["name"]]
        overs_bowled = stats.balls_bowled // 6
        overs_left = bowler_quota - overs_bowled
        base_score = float(p["bowl"])
        
        is_frontline = "Bowler" in p["role"] or base_score >= 80
        if is_frontline:
            base_score *= 3.0
        else:
            base_score *= 0.1 
        
        # Pitch adjustments
        if pitch == "Dusty" and "Spin" in p["role"]:
            base_score *= 1.5
        elif pitch == "Dry" and "Spin" in p["role"] and current_over >= 10:
            base_score *= 1.3
        elif pitch == "Green" and "Pace" in p["role"]:
            base_score *= 1.5
        elif pitch == "Hard" and "Pace" in p["role"] and current_over < 6:
            base_score *= 1.4
        elif pitch == "Cracked":
            base_score *= 1.3
        elif pitch == "Damp" and "Pace" in p["role"] and current_over < 6:
            base_score *= 1.6
        elif pitch == "Worn" and "Spin" in p["role"] and current_over >= 10:
            base_score *= 1.5
        elif pitch == "Dead":
            base_score *= 0.8
        elif pitch == "Turning" and "Spin" in p["role"]:
            base_score *= 2.0
        elif pitch == "Slow" and "Spin" in p["role"]:
            base_score *= 1.4
        elif pitch == "Bouncy" and "Pace" in p["role"]:
            base_score *= 1.5
        elif pitch == "Sticky":
            base_score *= 1.5
            
        # Weather Adjustments
        if weather == "Cloudy" and "Pace" in p["role"] and current_over < 6:
            base_score *= 1.1
        elif weather == "Overcast":
            if "Pace" in p["role"]: base_score *= 1.3
            elif "Spin" in p["role"]: base_score *= 0.8
        elif weather == "Humid" and "Pace" in p["role"]:
            base_score *= 1.2
        elif weather == "Dry Heat":
            if "Spin" in p["role"] and current_over >= 10:
                base_score *= 1.2
            elif "Pace" in p["role"] and current_over >= 10:
                base_score *= 0.8
        elif weather == "Windy" and "Pace" in p["role"]:
            base_score *= 1.3
        elif weather in ["Light Rain", "Drizzle"]:
            if "Spin" in p["role"]: base_score *= 0.6
            else: base_score *= 0.9
        elif weather in ["Heavy Rain", "Thunderstorm"]:
            if "Spin" in p["role"]: base_score *= 0.4
            else: base_score *= 0.7
        
        # Phase adjustments
        if current_over < 6: 
            if "Pace" in p["role"]:
                base_score *= 1.5
            if "Spin" in p["role"]:
                base_score *= 0.2 
        elif current_over < 15: 
            if "Spin" in p["role"]:
                base_score *= 1.5 
        else: 
            if "Pace" in p["role"] and p["archetype"] == "Finisher":
                base_score *= 2.0 
            if "Spin" in p["role"]:
                base_score *= 0.3 

        # Death Over Specialist Logic
        if current_over >= 16 and p["bowl"] >= 90 and overs_left > 0:
            base_score *= 50.0 

        # Prevent finishing pace bowlers from bowling out early
        if current_over < 15 and p["archetype"] == "Finisher" and "Pace" in p["role"]:
            if overs_left <= 2:
                base_score *= 0.1 
                
        # Form / Economy factor
        if overs_bowled > 0:
            eco = (stats.runs_conceded / max(1, stats.balls_bowled)) * 6
            if eco <= 6.0:
                base_score *= 2.5 
            elif eco > 11.0:
                base_score *= 0.3 
                
        weights.append(max(1.0, base_score))
        
    return random.choices(valid_bowlers, weights=weights, k=1)[0]

def execute_ball_math_t20(match):
    innings = match.current_innings
    striker = innings.batting_team["players"][innings.current_striker_idx]
    bowler = innings.current_bowler

    b_stats = innings.batting_stats[striker["name"]]
    bow_stats = innings.bowling_stats[bowler["name"]]

    bat_rating = striker["bat"] * b_stats.form_factor
    bowl_rating = bowler["bowl"] * bow_stats.form_factor
    
    # Pitch Mechanics
    if match.pitch == "Flat":
        bat_rating += 5
    elif match.pitch == "Green" and "Pace" in bowler["role"]:
        bowl_rating += 10
    elif match.pitch == "Dry" and "Spin" in bowler["role"] and innings.total_balls > 60:
        bowl_rating += 8
    elif match.pitch == "Dusty":
        if "Spin" in bowler["role"]: bowl_rating += 12
        bat_rating -= 3
    elif match.pitch == "Hard":
        if "Pace" in bowler["role"] and innings.total_balls < 36: bowl_rating += 8
        bat_rating += 4
    elif match.pitch == "Soft":
        bat_rating -= 5
    elif match.pitch == "Cracked":
        bowl_rating += 8
        bat_rating -= 6
    elif match.pitch == "Damp":
        if "Pace" in bowler["role"] and innings.total_balls < 36: bowl_rating += 12
        if innings.total_balls < 36: bat_rating -= 5
    elif match.pitch == "Dead":
        bat_rating += 10
        bowl_rating -= 5
    elif match.pitch == "Worn":
        if "Spin" in bowler["role"] and innings.total_balls > 60: bowl_rating += 10
        if innings.total_balls > 60: bat_rating -= 3
    elif match.pitch == "Turning":
        if "Spin" in bowler["role"]: bowl_rating += 15
        bat_rating -= 8
    elif match.pitch == "Two-Paced":
        bat_rating -= 8
    elif match.pitch == "Slow":
        if "Spin" in bowler["role"]: bowl_rating += 8
        bat_rating -= 6
    elif match.pitch == "Bouncy":
        if "Pace" in bowler["role"]: bowl_rating += 10
        bat_rating -= 2
    elif match.pitch == "Sticky":
        bowl_rating += 15
        bat_rating -= 12
        
    # Weather Mechanics
    if match.weather == "Clear":
        bat_rating += 3
    elif match.weather == "Cloudy" and "Pace" in bowler["role"]:
        bowl_rating += 4
    elif match.weather == "Overcast":
        if "Pace" in bowler["role"]: bowl_rating += 12
        bat_rating -= 5
    elif match.weather == "Humid" and "Pace" in bowler["role"]:
        bowl_rating += 8
    elif match.weather == "Dry Heat":
        if "Pace" in bowler["role"]: bowl_rating -= 5
        elif "Spin" in bowler["role"] and innings.total_balls > 60: bowl_rating += 8
    elif match.weather == "Windy":
        if "Pace" in bowler["role"]: bowl_rating += 6
    elif match.weather in ["Light Rain", "Drizzle"]:
        bowl_rating -= 8
        bat_rating += 4
    elif match.weather in ["Heavy Rain", "Thunderstorm"]:
        bowl_rating -= 12
        bat_rating += 8

    # Batter form progression
    if b_stats.balls_faced < 6:
        bat_rating -= 5
    elif 6 <= b_stats.balls_faced <= 45:
        bat_rating += 5
    elif b_stats.balls_faced > 45:
        bat_rating -= (b_stats.balls_faced - 45) * 0.5 
        
    # Bowler fatigue
    if bow_stats.balls_bowled >= 12 and "Pace" in bowler["role"]:
        bowl_rating -= 5
        
    is_powerplay = innings.total_balls < 36
    is_death_overs = innings.total_balls >= (match.max_balls - 30)
    
    pressure_multiplier = 1.0
    if match.current_innings_num == 2:
        target = getattr(match, "target", match.innings1.total_runs + 1)
        runs_needed = target - innings.total_runs
        balls_left = match.max_balls - innings.total_balls
        if balls_left > 0:
            rrr = (runs_needed / balls_left) * 6
            if rrr > 11.0:
                pressure_multiplier = min(1.4, 1.0 + ((rrr - 11.0) * 0.05))

    is_collapse = innings.over_log[-18:].count("🔴") >= 2 and innings.partnership_runs < 25
    is_set_partnership = innings.partnership_runs >= 30
    has_wickets_in_hand = innings.total_balls >= (match.max_balls - 42) and innings.wickets <= 3

    # Dynamic Delivery Generation based on Bowler Role (for Fast Sim)
    if match.current_delivery_selection:
        deliv = match.current_delivery_selection
    else:
        if "Spin" in bowler["role"]:
            if "Off" in bowler["role"]:
                deliv = random.choice(["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin", "Mystery"])
            else:
                deliv = random.choice(["Leg spin", "Googly", "Flipper", "Drifter", "Slider", "Mystery"])
        else:
            deliv = f"{random.choice(['Inswing', 'Outswing', 'Fast', 'Slow'])} {random.choice(['Bouncer', 'Full', 'Good', 'Yorker'])}"
            
    shot = match.current_shot_selection or get_smart_ai_shot_t20(deliv, is_collapse, is_death_overs, striker["archetype"])
        
    match.current_delivery_selection = None
    match.current_shot_selection = None
    match.temp_variation = None

    diff = bat_rating - bowl_rating
    
    # EXTRAS SYSTEM: Wide Check (Skips ball)
    if random.random() < 0.04 and "Yorker" not in deliv and "Slow" not in deliv:
        innings.total_runs += 1
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        bow_stats.runs_conceded += 1
        innings.over_log.append("WD")
        match.last_commentary = f"**{bowler['name']}** bowled a **Wide!**\n💥 **Result:** 1 Extra Run"
        return
        
    # EXTRAS SYSTEM: No Ball Check
    is_no_ball = False
    if random.random() < 0.02:
        is_no_ball = True
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        innings.total_runs += 1
        bow_stats.runs_conceded += 1

    dot_weight = max(15.0, 35.0 - diff * 0.4)
    single_weight = 40.0
    boundary_weight = max(2.0, 11.0 + diff * 0.4) # Nerfed exponential ceiling to stop 280+ T20 scores
    wicket_weight = max(1.5, 5.0 - diff * 0.15) 
    
    if b_stats.balls_faced > 45:
        wicket_weight *= 1.5

    bad_shot_selection = False
    perfect_shot_selection = False
    
    # Advanced Pitch Base Probability Modifiers
    if match.pitch == "Flat":
        boundary_weight *= 1.10
        wicket_weight *= 0.90
    elif match.pitch == "Green" and "Pace" in bowler["role"]:
        wicket_weight *= 1.20
        boundary_weight *= 0.85
    elif match.pitch == "Dusty" and "Spin" in bowler["role"]:
        wicket_weight *= 1.25
        boundary_weight *= 0.80
        dot_weight *= 1.15
    elif match.pitch == "Dry" and "Spin" in bowler["role"] and innings.total_balls > 60:
        wicket_weight *= 1.15
        dot_weight *= 1.10
    elif match.pitch == "Hard":
        if innings.total_balls < 36 and "Pace" in bowler["role"]:
            wicket_weight *= 1.15
        else:
            boundary_weight *= 1.05
    elif match.pitch == "Soft":
        dot_weight *= 1.30
        boundary_weight *= 0.70
    elif match.pitch == "Cracked":
        wicket_weight *= 1.30
        boundary_weight *= 0.80
    elif match.pitch == "Damp":
        if "Pace" in bowler["role"] and innings.total_balls < 36:
            wicket_weight *= 1.40
            boundary_weight *= 0.75
    elif match.pitch == "Dead":
        boundary_weight *= 1.25
        wicket_weight *= 0.80
    elif match.pitch == "Worn":
        if "Spin" in bowler["role"] and innings.total_balls > 60:
            wicket_weight *= 1.30
            dot_weight *= 1.20
            boundary_weight *= 0.80
    elif match.pitch == "Turning":
        if "Spin" in bowler["role"]:
            wicket_weight *= 1.40
            boundary_weight *= 0.65
            dot_weight *= 1.25
    elif match.pitch == "Two-Paced":
        dot_weight *= 1.40
        boundary_weight *= 0.70
        wicket_weight *= 1.10
    elif match.pitch == "Slow":
        dot_weight *= 1.30
        boundary_weight *= 0.65
        if "Spin" in bowler["role"]:
            wicket_weight *= 1.25
    elif match.pitch == "Bouncy":
        if "Pace" in bowler["role"]:
            wicket_weight *= 1.25
    elif match.pitch == "Sticky":
        wicket_weight *= 1.60
        boundary_weight *= 0.50
        dot_weight *= 1.50
        
    # Weather Advanced Modifiers
    if match.weather == "Overcast":
        wicket_weight *= 1.20
        boundary_weight *= 0.85
    elif match.weather == "Dry Heat":
        dot_weight *= 1.10
    elif match.weather == "Windy":
        wicket_weight *= 1.15
        boundary_weight *= 0.90
    elif match.weather in ["Light Rain", "Drizzle"]:
        wicket_weight *= 0.85
        boundary_weight *= 1.10
    elif match.weather in ["Heavy Rain", "Thunderstorm"]:
        wicket_weight *= 0.70
        boundary_weight *= 1.25
            
    # 🚨 TACTICAL USER BALANCING & SPIN LOGIC
    if "Yorker" in deliv:
        if shot in ["Pull", "Cut"]: bad_shot_selection = True
        elif shot in ["Defensive", "Drive"]: perfect_shot_selection = True
    elif "Bouncer" in deliv:
        if shot in ["Drive", "Sweep", "Scoop"]: bad_shot_selection = True
        elif shot in ["Pull", "Leave"]: perfect_shot_selection = True
    elif "Full Toss" in deliv:
        if shot in ["Defensive", "Leave"]: bad_shot_selection = True
        elif shot in ["Loft", "Drive"]: perfect_shot_selection = True
    elif deliv in SPIN_SHOT_MATRIX:
        if shot in SPIN_SHOT_MATRIX[deliv]: perfect_shot_selection = True
        elif shot == "Leave": bad_shot_selection = True 
        else:
            boundary_weight *= 0.20
            dot_weight *= 1.4
            single_weight *= 1.1

    if shot in ["Block", "Defensive"]:
        dot_weight *= 2.0; single_weight *= 0.8; boundary_weight = 0.2; wicket_weight *= 0.5
    elif shot == "Leave":
        dot_weight *= 3.0; single_weight = 0; boundary_weight = 0; wicket_weight *= 1.2
    else:
        if bad_shot_selection: wicket_weight *= 1.8; boundary_weight *= 0.3; dot_weight *= 1.5
        elif perfect_shot_selection: boundary_weight *= 1.4; wicket_weight *= 0.7
        
        if striker["archetype"] == "Aggressor": boundary_weight *= 1.2; wicket_weight *= 1.15
        elif striker["archetype"] == "Anchor": dot_weight *= 1.1; wicket_weight *= 0.75
        elif striker["archetype"] == "Finisher" and is_death_overs: boundary_weight *= 1.3

        if is_collapse: boundary_weight *= 0.6; wicket_weight *= 0.5 
        if is_set_partnership: wicket_weight *= 0.8
        if has_wickets_in_hand: boundary_weight *= 1.4; wicket_weight *= 1.3; dot_weight *= 0.6
        
        if is_death_overs or pressure_multiplier > 1.0:
            active_multiplier = max(1.3, pressure_multiplier) if is_death_overs else pressure_multiplier
            boundary_weight *= active_multiplier
            wicket_weight *= (active_multiplier * 1.1)
            
        if innings.last_ball_boundary: boundary_weight *= 1.15; wicket_weight *= 1.15
        if is_powerplay: boundary_weight *= 1.25; single_weight *= 0.85
            
    four_weight = boundary_weight
    six_weight = boundary_weight * 0.35
    
    if shot in ["Loft", "Scoop"]: four_weight *= 0.6; six_weight *= 3.0; wicket_weight *= 1.8; dot_weight *= 0.8
    elif shot in ["Block", "Defensive"]: four_weight *= 0.1; six_weight = 0.0
    elif shot in ["Drive", "Cut", "Pull", "Flick", "Sweep"]: four_weight *= 1.2; six_weight *= 0.5
        
    if "Slow" in deliv and shot in ["Loft", "Pull", "Sweep", "Scoop"]: wicket_weight *= 1.5; six_weight *= 0.5
    elif "Fast" in deliv and shot in ["Scoop", "Sweep", "Pull", "Loft"]: wicket_weight *= 1.5
    elif "Outswing" in deliv and shot in ["Drive", "Cut"]: wicket_weight *= 1.4; four_weight *= 1.2
    elif "Inswing" in deliv and shot in ["Drive", "Flick", "Sweep"]: wicket_weight *= 1.4

    # 🚨 ANTI-OVERCOOK SAFETIES (Prevents stacked conditions from breaking the game)
    four_weight = max(0.5, min(four_weight, 35.0)) # Hard cap to prevent 300+ scores
    six_weight = max(0.1, min(six_weight, 25.0))
    wicket_weight = max(1.0, min(wicket_weight, 30.0)) # Hard cap to prevent 10/10 scenarios
    dot_weight = max(5.0, dot_weight)

    weights = [dot_weight, single_weight, single_weight * 0.3, single_weight * 0.05, four_weight, six_weight, wicket_weight]
    outcome = random.choices(["dot", "single", "two", "three", "four", "six", "wicket"], weights=weights)[0]
    
    if is_no_ball and outcome == "wicket": outcome = "dot"
    
    b_stats.balls_faced += 1
    innings.last_ball_boundary = False
    outcome_text = ""

    if outcome == "wicket":
        innings.wickets += 1
        innings.partnership_runs = 0
        d_types = ["Bowled", "Caught", "LBW"]
        
        if "Outswing" in deliv and shot in ["Drive", "Cut"]: dismissal_type = "Caught Behind"
        elif "Inswing" in deliv and shot in ["Drive", "Flick"]: dismissal_type = random.choice(["Bowled", "LBW"])
        elif "Slow" in deliv and shot in ["Loft", "Pull", "Scoop"]: dismissal_type = "Caught"
        elif bad_shot_selection and "Yorker" in deliv: dismissal_type = "Bowled"
        elif bad_shot_selection and "Bouncer" in deliv: dismissal_type = "Caught"
        elif shot in ["Loft", "Scoop"]: dismissal_type = "Caught"
        else: dismissal_type = random.choice(d_types)
            
        if dismissal_type == "Bowled": b_stats.dismissal = f"b. {bowler['name']}"
        elif dismissal_type == "LBW": b_stats.dismissal = f"lbw b. {bowler['name']}"
        elif dismissal_type == "Caught Behind":
            wk = next((p["name"] for p in innings.bowling_team["players"] if "WK" in p["role"]), "Keeper")
            b_stats.dismissal = f"c. {wk} b. {bowler['name']}"
        else:
            fielders = [p["name"] for p in innings.bowling_team["players"] if p["name"] != bowler["name"]]
            fielder = random.choice(fielders) if fielders else "Fielder"
            b_stats.dismissal = f"c. {fielder} b. {bowler['name']}"
            
        bow_stats.wickets_taken += 1
        innings.over_log.append("🔴")
        outcome_text = f"WICKET! ({dismissal_type.upper()})"
        
        match.prev_striker_idx = innings.current_striker_idx
        if dismissal_type in ["LBW", "Caught Behind"] and match.simulation_mode == "interactive":
            match.pending_drs = True
            match.drs_dismissal = dismissal_type
        
        if innings.wickets < 10:
            is_ai_batting = match.is_ai_game and match.get_striker_user_id() == match.p2_id
            if match.simulation_mode == "whole_match" or is_ai_batting:
                innings.current_striker_idx = innings.next_batter_idx
                innings.next_batter_idx += 1
            else:
                match.pending_next_batter = True
                match.out_batter_profile = striker
    else:
        runs_map = {"dot": 0, "single": 1, "two": 2, "three": 3, "four": 4, "six": 6}
        runs = runs_map[outcome]
        
        is_bye = False
        if runs == 0 and random.random() < 0.05:
            is_bye = True
            runs = random.choice([1, 2, 4])
            innings.total_runs += runs
            if not hasattr(innings, 'extras'): innings.extras = 0
            innings.extras += runs
            outcome_text = f"{runs} Leg Byes"
            log_entry = f"{runs}LB"
        else:
            innings.total_runs += runs
            innings.partnership_runs += runs
            b_stats.runs_scored += runs
            bow_stats.runs_conceded += runs
            outcome_text = f"{runs} Runs" if runs > 0 else "Dot Ball"
                
            emoji_map = {0: "⚪", 1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "🟢", 6: "🔵"}
            log_entry = emoji_map[runs]
            
        if is_no_ball:
            log_entry = "NB" + (log_entry if runs > 0 and not is_bye else "")
            outcome_text += " (NO BALL)"
            
        if runs in [4, 6] and not is_bye:
            innings.last_ball_boundary = True
            
        innings.over_log.append(log_entry)
        
        if runs in [1, 3]:
            innings.current_striker_idx, innings.current_non_striker_idx = innings.current_non_striker_idx, innings.current_striker_idx

    if not is_no_ball:
        bow_stats.balls_bowled += 1
        innings.total_balls += 1
        
    match.last_commentary = f"**{bowler['name']}** bowled a **{deliv}**\n**{striker['name']}** played: **{shot}**\n💥 **Result:** {outcome_text}"