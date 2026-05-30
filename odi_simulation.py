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

def get_smart_ai_shot_odi(deliv, innings, is_death_overs, archetype):
    total_balls = innings.total_balls
    is_powerplay = total_balls < 60
    is_middle = 60 <= total_balls < 240
    is_collapse = ((innings.wickets >= 3 and total_balls < 120) or (innings.wickets >= 5 and total_balls < 240)) and innings.partnership_runs < 40

    if is_collapse:
        return random.choices(["Block", "Defensive", "Drive", "Leave"], weights=[40, 20, 30, 10], k=1)[0]
        
    if is_death_overs:
        if archetype == "Anchor":
            return random.choices(["Drive", "Loft", "Pull", "Flick"], weights=[30, 30, 20, 20], k=1)[0]
        else:
            return random.choices(["Loft", "Pull", "Scoop", "Sweep"], weights=[40, 25, 15, 20], k=1)[0]
            
    # ODI Specific Match Phase AI
    if is_powerplay:
        if "Yorker" in deliv or "Good" in deliv:
            return random.choices(["Block", "Drive", "Flick"], weights=[50, 35, 15], k=1)[0]
        elif "Bouncer" in deliv:
            return random.choices(["Leave", "Pull", "Block"], weights=[40, 20, 40], k=1)[0]
        else:
            return random.choices(["Drive", "Cut", "Flick", "Block"], weights=[40, 20, 20, 20], k=1)[0]
    elif is_middle:
        if deliv in SPIN_SHOT_MATRIX:
            if random.random() < 0.7:
                return random.choice(SPIN_SHOT_MATRIX[deliv])
            return random.choices(["Drive", "Sweep", "Cut", "Block"], weights=[35, 25, 25, 15], k=1)[0]
        else:
            return random.choices(["Drive", "Cut", "Flick", "Pull"], weights=[40, 20, 25, 15], k=1)[0]
            
    return random.choices(["Drive", "Cut", "Flick", "Block"], weights=[35, 25, 25, 15], k=1)[0]

def get_smart_ai_bowler_odi(innings, pitch, format_overs=50):
    valid_bowlers = []
    bowler_quota = 10
    
    for p in innings.bowling_team["players"]:
        if ("Bowler" in p["role"] or "All-Rounder" in p["role"]):
            stats = innings.bowling_stats[p["name"]]
            if (stats.balls_bowled // 6) < bowler_quota:
                if not innings.current_bowler or innings.current_bowler["name"] != p["name"]:
                    valid_bowlers.append(p)
                    
    if not valid_bowlers: return None

    current_over = innings.total_balls // 6
    weights = []
    
    for p in valid_bowlers:
        stats = innings.bowling_stats[p["name"]]
        overs_bowled = stats.balls_bowled // 6
        base_score = float(p["bowl"])
        
        is_frontline = "Bowler" in p["role"] or base_score >= 80
        base_score *= (3.0 if is_frontline else 0.1)
        
        # ODI Pitch Adjustments
        if pitch == "Dusty" and "Spin" in p["role"]: base_score *= 1.5
        elif pitch == "Green" and "Pace" in p["role"]: base_score *= 1.5
        
        # ODI Phase Adjustments
        if current_over < 10: 
            if "Pace" in p["role"]: base_score *= 2.0
            if "Spin" in p["role"]: base_score *= 0.1 
        elif 10 <= current_over < 40: 
            if "Spin" in p["role"]: base_score *= 2.5 
            if "Pace" in p["role"]: base_score *= 0.5
        else: 
            if "Pace" in p["role"]: base_score *= 2.5 
            if "Spin" in p["role"]: base_score *= 0.3 

        if overs_bowled > 0:
            eco = (stats.runs_conceded / max(1, stats.balls_bowled)) * 6
            if eco <= 5.0: base_score *= 2.0 
            elif eco > 8.0: base_score *= 0.3 
                
        weights.append(max(1.0, base_score))
        
    return random.choices(valid_bowlers, weights=weights, k=1)[0]

def execute_ball_math_odi(match):
    innings = match.current_innings
    striker = innings.batting_team["players"][innings.current_striker_idx]
    bowler = innings.current_bowler

    b_stats = innings.batting_stats[striker["name"]]
    bow_stats = innings.bowling_stats[bowler["name"]]

    bat_rating = striker["bat"] * b_stats.form_factor
    bowl_rating = bowler["bowl"] * bow_stats.form_factor
    
    if match.pitch == "Dusty" and "Spin" in bowler["role"]: bowl_rating += 10
    elif match.pitch == "Green" and "Pace" in bowler["role"]: bowl_rating += 10
    elif match.pitch == "Flat": bat_rating += 10

    # ODI Batter form progression (Realistic pacing & late fatigue prevents 180+ spam)
    if b_stats.balls_faced < 15:
        bat_rating -= 15
    elif 15 <= b_stats.balls_faced < 40:
        bat_rating -= 5
    elif 40 <= b_stats.balls_faced <= 80:
        bat_rating += 5
    elif 80 < b_stats.balls_faced <= 120:
        bat_rating += 10
    elif b_stats.balls_faced > 120:
        bat_rating -= 5
        
    # ODI Bowler fatigue
    if bow_stats.balls_bowled >= 42 and "Pace" in bowler["role"]:
        bowl_rating -= 5
    elif bow_stats.balls_bowled >= 54:
        bowl_rating -= 10
        
    total_balls = innings.total_balls
    is_powerplay = total_balls < 60
    is_middle = 60 <= total_balls < 240
    is_death_overs = total_balls >= 240
    
    is_collapse = ((innings.wickets >= 3 and total_balls < 120) or (innings.wickets >= 5 and total_balls < 240)) and innings.partnership_runs < 40

    pressure_multiplier = 1.0
    if match.current_innings_num == 2 and not is_collapse:
        runs_needed = (match.innings1.total_runs + 1) - innings.total_runs
        balls_left = match.max_balls - total_balls
        if balls_left > 0:
            rrr = (runs_needed / balls_left) * 6
            
            # Smart Chasing Phase: Teams delay heavy panic until the later overs
            if total_balls < 120:  # Overs 1-20
                threshold = 8.5
                max_p = 1.20
                scale = 0.08
            elif total_balls < 210:  # Overs 21-35
                threshold = 7.5
                max_p = 1.40
                scale = 0.10
            else:  # Overs 36-50
                threshold = 6.5
                max_p = 1.85
                scale = 0.15
                
            if rrr > threshold:
                pressure_multiplier = min(max_p, 1.0 + ((rrr - threshold) * scale))

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
            
    shot = match.current_shot_selection or get_smart_ai_shot_odi(deliv, innings, is_death_overs, striker["archetype"])
        
    match.current_delivery_selection = None
    match.current_shot_selection = None
    match.temp_variation = None

    diff = bat_rating - bowl_rating
    
    # Extras
    if random.random() < 0.04 and "Yorker" not in deliv and "Slow" not in deliv:
        innings.total_runs += 1
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        bow_stats.runs_conceded += 1
        innings.over_log.append("WD")
        match.last_commentary = f"**{bowler['name']}** bowled a **Wide!**\n💥 **Result:** 1 Extra Run"
        return
        
    is_no_ball = False
    if random.random() < 0.01:
        is_no_ball = True
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        innings.total_runs += 1
        bow_stats.runs_conceded += 1

    # Baseline ODI Weights - High discipline, lower boundary frequency
    dot_weight = max(25.0, 50.0 - diff * 0.4)
    single_weight = 40.0
    boundary_weight = max(1.5, 7.0 + diff * 0.3) 
    wicket_weight = max(1.2, 3.0 - diff * 0.1) 
    
    # Pitch Extreme Modifiers (Balanced)
    if match.pitch == "Green" and "Pace" in bowler["role"]:
        wicket_weight *= 1.25
        boundary_weight *= 0.85
    elif match.pitch == "Dusty" and "Spin" in bowler["role"]:
        wicket_weight *= 1.25
        boundary_weight *= 0.80
        dot_weight *= 1.15
    elif match.pitch == "Flat":
        boundary_weight *= 1.12
        wicket_weight *= 0.95

    if is_powerplay:
        if "Pace" in bowler["role"]:
            wicket_weight *= 1.20 
        boundary_weight *= 1.15 
        dot_weight *= 1.10
    elif is_middle:
        single_weight *= 1.35 # Strike rotation
        dot_weight *= 0.85
        boundary_weight *= 0.80 

    bad_shot_selection = False
    perfect_shot_selection = False
    
    # Tactical Spin & Pace UI
    if "Yorker" in deliv and shot in ["Pull", "Cut"]: bad_shot_selection = True
    elif "Bouncer" in deliv and shot in ["Drive", "Sweep", "Scoop"]: bad_shot_selection = True
    elif "Full Toss" in deliv and shot in ["Defensive", "Leave"]: bad_shot_selection = True
    elif deliv in SPIN_SHOT_MATRIX and shot in SPIN_SHOT_MATRIX[deliv]: perfect_shot_selection = True

    if shot in ["Block", "Defensive"]:
        dot_weight *= 2.0
        single_weight *= 0.6
        boundary_weight = 0.1
        wicket_weight *= 0.4
    elif shot == "Leave":
        dot_weight *= 3.0
        single_weight = 0
        boundary_weight = 0
        wicket_weight *= 0.6
    else:
        if bad_shot_selection:
            wicket_weight *= 1.8
            boundary_weight *= 0.3
            dot_weight *= 1.5
        elif perfect_shot_selection:
            boundary_weight *= 1.3
            single_weight *= 1.2
            wicket_weight *= 0.8
            
        if is_collapse:
            boundary_weight *= 0.5
            wicket_weight *= 0.6 
            
        # Massive late assault if they have protected their wickets properly
        if total_balls >= 240 and innings.wickets <= 4:
            boundary_weight *= 1.25
            wicket_weight *= 1.15
            dot_weight *= 0.7
            
        if is_death_overs or pressure_multiplier > 1.0:
            active_multiplier = max(1.4, pressure_multiplier) if is_death_overs else pressure_multiplier
            boundary_weight *= active_multiplier
            
            if total_balls < 180:
                wicket_weight *= (active_multiplier * 1.05) # Calculated risks while building
            else:
                wicket_weight *= (active_multiplier * 1.15) # High risks when running out of time
            
    four_weight = boundary_weight
    six_weight = boundary_weight * 0.25
    
    # ODI Exploit fixes
    if shot in ["Loft", "Scoop"]:
        four_weight *= 0.7
        six_weight *= 2.5
        if not is_death_overs: 
            wicket_weight *= 3.0 # Highly suicidal in overs 1-40
        else:
            wicket_weight *= 1.5
        dot_weight *= 0.8
    elif shot in ["Block", "Defensive", "Leave"]:
        four_weight = 0.0
        six_weight = 0.0
    elif shot in ["Drive", "Cut", "Pull", "Flick", "Sweep"]:
        four_weight *= 1.2
        six_weight *= 0.3

    weights = [dot_weight, single_weight, single_weight * 0.4, single_weight * 0.05, four_weight, six_weight, wicket_weight]
    outcome = random.choices(["dot", "single", "two", "three", "four", "six", "wicket"], weights=weights)[0]
    
    if is_no_ball and outcome == "wicket": outcome = "dot"
    b_stats.balls_faced += 1

    if outcome == "wicket":
        innings.wickets += 1
        innings.partnership_runs = 0
        dismissal_type = random.choice(["Bowled", "Caught", "LBW", "Caught Behind"])
        b_stats.dismissal = f"b. {bowler['name']}" if dismissal_type == "Bowled" else f"lbw b. {bowler['name']}" if dismissal_type == "LBW" else f"c. {dismissal_type} b. {bowler['name']}"
        
        if dismissal_type == "Bowled":
            b_stats.dismissal = f"b. {bowler['name']}"
        elif dismissal_type == "LBW":
            b_stats.dismissal = f"lbw b. {bowler['name']}"
        elif dismissal_type == "Caught Behind":
            wk = next((p["name"] for p in innings.bowling_team["players"] if "WK" in p["role"]), "Keeper")
            b_stats.dismissal = f"c. {wk} b. {bowler['name']}"
        else:
            fielders = [p["name"] for p in innings.bowling_team["players"] if p["name"] != bowler["name"]]
            fielder = random.choice(fielders) if fielders else "Fielder"
            b_stats.dismissal = f"c. {fielder} b. {bowler['name']}"
            
        bow_stats.wickets_taken += 1
        innings.over_log.append("🔴")
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
        match.last_commentary = f"**{bowler['name']}** bowled a **{deliv}**\n**{striker['name']}** played: **{shot}**\n💥 **WICKET! ({dismissal_type.upper()})**"
    else:
        runs = {"dot": 0, "single": 1, "two": 2, "three": 3, "four": 4, "six": 6}[outcome]
        innings.total_runs += runs
        innings.partnership_runs += runs
        b_stats.runs_scored += runs
        bow_stats.runs_conceded += runs
        innings.over_log.append({0: "⚪", 1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "🟢", 6: "🔵"}[runs])
        if runs in [1, 3]: innings.current_striker_idx, innings.current_non_striker_idx = innings.current_non_striker_idx, innings.current_striker_idx
        match.last_commentary = f"**{bowler['name']}** bowled a **{deliv}**\n**{striker['name']}** played: **{shot}**\n💥 **Result:** {runs} Runs"

    if not is_no_ball:
        bow_stats.balls_bowled += 1
        innings.total_balls += 1
