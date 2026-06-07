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

def get_smart_ai_shot_odi(deliv, innings, is_death_overs, archetype, pressure_multiplier=1.0):
    total_balls = innings.total_balls
    is_powerplay = total_balls < 60
    is_middle = 60 <= total_balls < 240
    is_collapse = ((innings.wickets >= 3 and total_balls < 120) or (innings.wickets >= 5 and total_balls < 240)) and innings.partnership_runs < 40

    # High pressure forces aggressive mindset (RRR > ~7-8 depending on phase)
    force_aggression = pressure_multiplier > 1.2 or is_death_overs
        
    if is_collapse and not force_aggression:
        if "Yorker" in deliv: return random.choices(["Block", "Defensive", "Drive"], weights=[40, 30, 30], k=1)[0]
        elif "Bouncer" in deliv: return random.choices(["Leave", "Block", "Pull"], weights=[40, 40, 20], k=1)[0]
        else: return random.choices(["Block", "Defensive", "Drive", "Flick"], weights=[30, 30, 25, 15], k=1)[0]

    if force_aggression:
        if "Yorker" in deliv:
            return random.choices(["Drive", "Block", "Flick", "Scoop", "Pull"], weights=[40, 10, 20, 20, 10], k=1)[0]
        elif "Bouncer" in deliv:
            return random.choices(["Pull", "Cut", "Loft", "Drive"], weights=[40, 30, 20, 10], k=1)[0]
        elif "Full" in deliv:
            return random.choices(["Loft", "Drive", "Sweep", "Scoop"], weights=[40, 30, 15, 15], k=1)[0]
        elif deliv in SPIN_SHOT_MATRIX:
            if random.random() < 0.8: return random.choice(SPIN_SHOT_MATRIX[deliv])
            return random.choices(["Loft", "Sweep", "Drive"], weights=[40, 40, 20], k=1)[0]
        else:
            return random.choices(["Loft", "Pull", "Drive", "Scoop"], weights=[30, 30, 25, 15], k=1)[0]
            
    # Standard Match Phase AI
    if "Yorker" in deliv:
        return random.choices(["Block", "Defensive", "Drive", "Flick", "Cut"], weights=[35, 25, 20, 10, 10], k=1)[0]
    elif "Bouncer" in deliv:
        return random.choices(["Leave", "Block", "Pull", "Cut", "Drive"], weights=[25, 25, 25, 15, 10], k=1)[0]
    elif "Full Toss" in deliv or "Full" in deliv:
        return random.choices(["Drive", "Flick", "Loft", "Block", "Leave"], weights=[45, 25, 15, 10, 5], k=1)[0]
    elif deliv in SPIN_SHOT_MATRIX:
        if random.random() < 0.65:
            return random.choice(SPIN_SHOT_MATRIX[deliv])
        return random.choices(["Drive", "Sweep", "Cut", "Block", "Leave"], weights=[30, 20, 20, 20, 10], k=1)[0]
    else:
        return random.choices(["Drive", "Cut", "Flick", "Block", "Loft"], weights=[30, 25, 25, 15, 5], k=1)[0]

def get_smart_ai_bowler_odi(innings, pitch, weather="Clear", format_overs=50):
    valid_bowlers = []
    bowler_quota = 10
    bowler_quota = max(1, (format_overs + 4) // 5) # Scales perfectly for Custom match formats!
    
    for p in innings.bowling_team["players"]:
        if not getattr(innings.bowling_stats.get(p["name"]), "is_subbed_out", False):
            if ("Bowler" in p["role"] or "All-Rounder" in p["role"]):
                stats = innings.bowling_stats[p["name"]]
                if (stats.balls_bowled // 6) < bowler_quota:
                    if not innings.current_bowler or innings.current_bowler["name"] != p["name"]:
                        valid_bowlers.append(p)
                    
    if not valid_bowlers: return None
    # FALLBACK 1: If no standard bowlers have overs left, allow Batters to bowl
    if not valid_bowlers:
        for p in innings.bowling_team["players"]:
            if not getattr(innings.bowling_stats.get(p["name"]), "is_subbed_out", False):
                stats = innings.bowling_stats[p["name"]]
                if (stats.balls_bowled // 6) < bowler_quota:
                    if not innings.current_bowler or innings.current_bowler["name"] != p["name"]:
                        valid_bowlers.append(p)
                    
    # FALLBACK 2: If everyone has bowled two in a row
    if not valid_bowlers:
        for p in innings.bowling_team["players"]:
            if not getattr(innings.bowling_stats.get(p["name"]), "is_subbed_out", False):
                stats = innings.bowling_stats[p["name"]]
                if (stats.balls_bowled // 6) < bowler_quota:
                    valid_bowlers.append(p)
                
    # FALLBACK 3: Absolute worst case, ignore quotas completely
    if not valid_bowlers:
        valid_bowlers = [p for p in innings.bowling_team["players"] if not getattr(innings.bowling_stats.get(p["name"]), "is_subbed_out", False)]

    current_over = innings.total_balls // 6
    weights = []
    
    for p in valid_bowlers:
        stats = innings.bowling_stats[p["name"]]
        overs_bowled = stats.balls_bowled // 6
        
        is_frontline = "Bowler" in p["role"] or float(p["bowl"]) >= 80
        base_score = (float(p["bowl"]) / 10.0) ** 2.0 # Toned down to allow slight bowler rotation
        base_score *= (3.0 if is_frontline else 0.1)
        
        # ODI Pitch Adjustments
        if pitch == "Dusty" and "Spin" in p["role"]: 
            base_score *= 1.5
        elif pitch == "Dry" and "Spin" in p["role"] and current_over >= 25: 
            base_score *= 1.4
        elif pitch == "Green" and "Pace" in p["role"]: 
            base_score *= 1.5
        elif pitch == "Hard" and "Pace" in p["role"] and current_over < 10: 
            base_score *= 1.4
        elif pitch == "Cracked":
            base_score *= 1.3
        elif pitch == "Damp" and "Pace" in p["role"] and current_over < 15:
            base_score *= 1.6
        elif pitch == "Worn" and "Spin" in p["role"] and current_over >= 25:
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
        if weather == "Cloudy" and "Pace" in p["role"] and current_over < 10:
            base_score *= 1.1
        elif weather == "Overcast":
            if "Pace" in p["role"]: base_score *= 1.4
            elif "Spin" in p["role"]: base_score *= 0.7
        elif weather == "Humid" and "Pace" in p["role"]:
            base_score *= 1.2
        elif weather == "Dry Heat":
            if "Spin" in p["role"] and current_over >= 25:
                base_score *= 1.3
            elif "Pace" in p["role"] and current_over >= 25:
                base_score *= 0.7
        elif weather == "Windy" and "Pace" in p["role"]:
            base_score *= 1.3
        elif weather in ["Light Rain", "Drizzle"]:
            if "Spin" in p["role"]: base_score *= 0.6
            else: base_score *= 0.9
        elif weather in ["Heavy Rain", "Thunderstorm"]:
            if "Spin" in p["role"]: base_score *= 0.4
            else: base_score *= 0.7
        
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
    
    if match.pitch == "Flat":
        bat_rating += 5
    elif match.pitch == "Green" and "Pace" in bowler["role"]:
        bowl_rating += 5
    elif match.pitch == "Dry" and "Spin" in bowler["role"] and innings.total_balls > 150:
        bowl_rating += 5
    elif match.pitch == "Dusty":
        if "Spin" in bowler["role"]: bowl_rating += 3
        bat_rating -= 2
    elif match.pitch == "Hard":
        if "Pace" in bowler["role"] and innings.total_balls < 60: bowl_rating += 4
        bat_rating += 3
    elif match.pitch == "Soft":
        bat_rating -= 3
    elif match.pitch == "Cracked":
        bowl_rating += 3
        bat_rating -= 2
    elif match.pitch == "Damp":
        if "Pace" in bowler["role"] and innings.total_balls < 90: bowl_rating += 4
        if innings.total_balls < 90: bat_rating -= 3
    elif match.pitch == "Dead":
        bat_rating += 4
        bowl_rating -= 3
    elif match.pitch == "Worn":
        if "Spin" in bowler["role"] and innings.total_balls > 150: bowl_rating += 5
        if innings.total_balls > 150: bat_rating -= 2
    elif match.pitch == "Turning":
        if "Spin" in bowler["role"]: bowl_rating += 5
        bat_rating -= 2
    elif match.pitch == "Two-Paced":
        bat_rating -= 2
    elif match.pitch == "Slow":
        if "Spin" in bowler["role"]: bowl_rating += 4
        bat_rating -= 2
    elif match.pitch == "Bouncy":
        if "Pace" in bowler["role"]: bowl_rating += 5
        bat_rating -= 1
    elif match.pitch == "Sticky":
        bowl_rating += 5
        bat_rating -= 2
        
    # Weather Mechanics — new-ball conditions scale with innings.total_balls so the
    # advantage applies to the START of BOTH innings equally (not just innings 1).
    _new_ball = innings.total_balls < 90   # first 15 overs of whichever innings
    _mid_ball = innings.total_balls < 180  # overs 15-30
    if match.weather == "Clear":
        bat_rating += 3
    elif match.weather == "Cloudy" and "Pace" in bowler["role"]:
        bowl_rating += (4 if _new_ball else 1 if _mid_ball else 0)
    elif match.weather == "Overcast":
        if "Pace" in bowler["role"]: bowl_rating += (5 if _new_ball else 2 if _mid_ball else 1)
        bat_rating -= (2 if _new_ball else 1 if _mid_ball else 0)
    elif match.weather == "Humid" and "Pace" in bowler["role"]:
        bowl_rating += (5 if _new_ball else 2 if _mid_ball else 0)
    elif match.weather == "Dry Heat":
        if "Pace" in bowler["role"]: bowl_rating -= 5
        elif "Spin" in bowler["role"] and innings.total_balls > 150: bowl_rating += 5
    elif match.weather == "Windy":
        if "Pace" in bowler["role"]: bowl_rating += (5 if _new_ball else 3 if _mid_ball else 1)
    elif match.weather in ["Light Rain", "Drizzle"]:
        bowl_rating -= 4
        bat_rating += 2
    elif match.weather in ["Heavy Rain", "Thunderstorm"]:
        bowl_rating -= 3
        bat_rating += 2

    # ODI Batter form progression (Realistic pacing & late fatigue prevents 180+ spam)
    if b_stats.balls_faced < 10:
        bat_rating -= 3
    elif 10 <= b_stats.balls_faced < 30:
        bat_rating -= 1
    elif 30 <= b_stats.balls_faced <= 80:
        bat_rating += 4
    elif 80 < b_stats.balls_faced <= 120:
        bat_rating += 7
    elif b_stats.balls_faced > 120:
        bat_rating -= 2
        
    # ODI Bowler fatigue
    if bow_stats.balls_bowled >= 42 and "Pace" in bowler["role"]:
        bowl_rating -= 3
    elif bow_stats.balls_bowled >= 54:
        bowl_rating -= 5
        
    total_balls = innings.total_balls
    is_powerplay = total_balls < 60
    is_middle = 60 <= total_balls < 240
    is_death_overs = total_balls >= 240
    
    is_collapse = ((innings.wickets >= 3 and total_balls < 120) or (innings.wickets >= 5 and total_balls < 240)) and innings.partnership_runs < 40
    is_set_partnership = innings.partnership_runs >= 50
    has_wickets_in_hand = innings.total_balls >= 240 and innings.wickets <= 4

    pressure_multiplier = 1.0
    runs_needed = 0
    balls_left = match.max_balls - total_balls
    if match.current_innings_num == 2 and not is_collapse:
        target = getattr(match, "target", match.innings1.total_runs + 1)
        runs_needed = target - innings.total_runs
        if balls_left > 0:
            rrr = (runs_needed / balls_left) * 6
            
            # Smart Chasing Phase: Teams delay heavy panic until the later overs
            if total_balls < 120:  # Overs 1-20
                threshold = 8.0
                max_p = 1.20
                scale = 0.05
            elif total_balls < 210:  # Overs 21-35
                threshold = 7.5
                max_p = 1.35
                scale = 0.08
            else:  # Overs 36-50
                threshold = 6.5
                max_p = 1.60
                scale = 0.12
                
            if rrr > threshold:
                pressure_multiplier = min(max_p, 1.0 + ((rrr - threshold) * scale))

    if match.current_delivery_selection:
        deliv = match.current_delivery_selection
    else:
        if "Spin" in bowler["role"]:
            if "Off" in bowler["role"]:
                opts = ["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin", "Mystery"]
            else:
                opts = ["Leg spin", "Googly", "Flipper", "Drifter", "Slider", "Mystery"]
            if getattr(innings, "mystery_bowled_this_over", False):
                opts.remove("Mystery")
            deliv = random.choice(opts)
            if deliv == "Mystery":
                innings.mystery_bowled_this_over = True
        else:
            deliv = f"{random.choice(['Inswing', 'Outswing', 'Fast', 'Slow'])} {random.choice(['Bouncer', 'Full', 'Good', 'Yorker'])}"
            
    shot = match.current_shot_selection or get_smart_ai_shot_odi(deliv, innings, is_death_overs, striker["archetype"], pressure_multiplier)
        
    match.current_delivery_selection = None
    match.current_shot_selection = None
    match.temp_variation = None

    diff = bat_rating - bowl_rating
    
    free_hit_active = getattr(match, "free_hit", False)
    is_wide = False
    is_no_ball = False
    prefix = getattr(match, "last_commentary_prefix", "")
    match.last_commentary_prefix = ""
    
    if not hasattr(innings, "bouncers_in_over"): innings.bouncers_in_over = 0
    is_bouncer_no_ball = False
    if "Bouncer" in deliv:
        innings.bouncers_in_over += 1
        if innings.bouncers_in_over == 2:  # ODI: 1 bouncer per over, 2nd = no ball
            is_no_ball = True
            is_bouncer_no_ball = True
            prefix += "🚨 **NO BALL!** (Second bouncer of the over — ODI limit is 1)\n"
            # Bouncer no balls do NOT give free hits in ODI (ICC rules)

    if not is_no_ball and random.random() < 0.04 and "Yorker" not in deliv and "Slow" not in deliv:
        is_wide = True
        innings.total_runs += 1
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        bow_stats.runs_conceded += 1
        innings.over_log.append("WD")
        match.last_commentary = prefix + f"**{bowler['name']}** bowled a **Wide!**\n💥 **Result:** 1 Extra Run"
        if free_hit_active: match.last_commentary_prefix = "🛡️ *(Free Hit continues)*\n"
        return

    if not is_no_ball and random.random() < 0.01:
        is_no_ball = True
        if not free_hit_active:
            prefix += "🚨 **NO BALL!** Overstepping!\n➡️ **NEXT BALL IS A FREE HIT!**\n"
        else:
            prefix += "🚨 **NO BALL!** (Still a Free Hit)\n"

    if is_no_ball:
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        innings.total_runs += 1
        bow_stats.runs_conceded += 1
        if not is_bouncer_no_ball:  # Only front-foot no balls give free hits in ODI
            match.free_hit = True

    # Baseline ODI Weights - High discipline, lower boundary frequency
    dot_weight = max(22.0, 50.0 - diff * 0.45)
    single_weight = 40.0
    boundary_weight = max(1.5, 7.0 + diff * 0.35) # Balanced to allow underdog resistance
    wicket_weight = max(1.0, 3.0 - diff * 0.15) # Balanced to allow upsets
    
    # Pitch Extreme Modifiers (Balanced)
    if match.pitch == "Green" and "Pace" in bowler["role"]:
        wicket_weight *= 1.15
        boundary_weight *= 0.90
    elif match.pitch == "Dusty" and "Spin" in bowler["role"]:
        wicket_weight *= 1.15
        boundary_weight *= 0.85
        dot_weight *= 1.10
    elif match.pitch == "Dry" and "Spin" in bowler["role"] and total_balls > 150:
        wicket_weight *= 1.10
        dot_weight *= 1.05
    elif match.pitch == "Hard":
        if total_balls < 60 and "Pace" in bowler["role"]:
            wicket_weight *= 1.10
        else:
            boundary_weight *= 1.05
    elif match.pitch == "Soft":
        dot_weight *= 1.20
        boundary_weight *= 0.80
    elif match.pitch == "Cracked":
        wicket_weight *= 1.20
        boundary_weight *= 0.85
    elif match.pitch == "Damp":
        if "Pace" in bowler["role"] and total_balls < 90:
            wicket_weight *= 1.25
            boundary_weight *= 0.80
    elif match.pitch == "Dead":
        boundary_weight *= 1.20
        wicket_weight *= 0.85
    elif match.pitch == "Worn":
        if "Spin" in bowler["role"] and total_balls > 150:
            wicket_weight *= 1.20
            dot_weight *= 1.10
            boundary_weight *= 0.85
    elif match.pitch == "Turning":
        if "Spin" in bowler["role"]:
            wicket_weight *= 1.25
            boundary_weight *= 0.75
            dot_weight *= 1.15
    elif match.pitch == "Two-Paced":
        dot_weight *= 1.25
        boundary_weight *= 0.80
        wicket_weight *= 1.10
    elif match.pitch == "Slow":
        dot_weight *= 1.20
        boundary_weight *= 0.75
        if "Spin" in bowler["role"]:
            wicket_weight *= 1.15
    elif match.pitch == "Bouncy":
        if "Pace" in bowler["role"]:
            wicket_weight *= 1.15
    elif match.pitch == "Sticky":
        wicket_weight *= 1.40
        boundary_weight *= 0.65
        dot_weight *= 1.35
    elif match.pitch == "Flat":
        boundary_weight *= 1.10
        wicket_weight *= 0.95
        
    # Weather Advanced Modifiers — innings.total_balls used so both innings
    # get the new-ball swing boost, not just innings 1.
    _new_ball = total_balls < 90   # first 15 overs
    _mid_ball = total_balls < 180  # overs 15-30
    if match.weather == "Overcast":
        wicket_weight *= (1.30 if _new_ball else 1.15 if _mid_ball else 1.05)
        boundary_weight *= (0.80 if _new_ball else 0.88 if _mid_ball else 0.95)
    elif match.weather == "Cloudy":
        if _new_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.12
            boundary_weight *= 0.93
        elif _mid_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.05
    elif match.weather == "Humid":
        if _new_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.10
        elif _mid_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.04
    elif match.weather == "Dry Heat":
        dot_weight *= 1.10
    elif match.weather == "Windy":
        wicket_weight *= (1.20 if _new_ball else 1.10 if _mid_ball else 1.05)
        boundary_weight *= (0.87 if _new_ball else 0.92 if _mid_ball else 0.97)
    elif match.weather in ["Light Rain", "Drizzle"]:
        wicket_weight *= 0.85
        boundary_weight *= 1.10
    elif match.weather in ["Heavy Rain", "Thunderstorm"]:
        wicket_weight *= 0.70
        boundary_weight *= 1.25

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
    if "Yorker" in deliv:
        if shot in ["Pull", "Cut", "Leave"]: bad_shot_selection = True
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
            boundary_weight *= 0.25
            dot_weight *= 1.3
            single_weight *= 1.1

    if shot in ["Block", "Defensive"]:
        dot_weight *= 1.6; single_weight *= 0.9; boundary_weight = 0.1; wicket_weight *= 0.5
    elif shot == "Leave":
        dot_weight *= 3.0; single_weight = 0.0; boundary_weight = 0.0; wicket_weight *= 1.2
    else:
        if bad_shot_selection:
            wicket_weight *= 1.8; boundary_weight *= 0.3; dot_weight *= 1.5
        elif perfect_shot_selection:
            boundary_weight *= 1.3; single_weight *= 1.1; wicket_weight *= 0.8
            
        if striker["archetype"] == "Aggressor": 
            boundary_weight *= 1.15; wicket_weight *= 1.15
        elif striker["archetype"] == "Anchor": 
            if b_stats.balls_faced >= 40 and (is_death_overs or pressure_multiplier > 1.15):
                boundary_weight *= 1.15; wicket_weight *= 1.05 # Set Anchors slog effectively!
            else:
                dot_weight *= 1.1; wicket_weight *= 0.8
        elif striker["archetype"] == "Finisher" and is_death_overs: 
            boundary_weight *= 1.25

        if is_collapse: boundary_weight *= 0.7; wicket_weight *= 0.75; single_weight *= 1.2
        if is_set_partnership: wicket_weight *= 0.85
        if has_wickets_in_hand: boundary_weight *= 1.3; wicket_weight *= 1.2; dot_weight *= 0.7
            
        active_multiplier = pressure_multiplier
        if is_death_overs:
            if match.current_innings_num == 1:
                active_multiplier = max(1.35, pressure_multiplier)
            else:
                if balls_left > 0 and (runs_needed / balls_left * 6) > 6.5:
                    active_multiplier = max(1.35, pressure_multiplier)
                    
        if active_multiplier > 1.0:
            boundary_weight *= active_multiplier
            if total_balls < 240:
                wicket_weight *= (1.0 + (active_multiplier - 1.0) * 0.5)
            else:
                wicket_weight *= (1.0 + (active_multiplier - 1.0) * 0.8)
                
        if innings.last_ball_boundary: boundary_weight *= 1.15; wicket_weight *= 1.15
            
    if "Mystery" in deliv:
        wicket_weight *= 1.6
        dot_weight *= 1.5
        boundary_weight *= 0.6
        single_weight *= 0.8

    four_weight = boundary_weight
    six_weight = boundary_weight * 0.25
    
    # ODI Exploit fixes
    if shot in ["Loft", "Scoop"]:
        four_weight *= 0.6
        six_weight *= 3.0
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
        six_weight *= 0.4
        
    if "Slow" in deliv and shot in ["Loft", "Pull", "Sweep", "Scoop"]: wicket_weight *= 1.5; six_weight *= 0.5
    elif "Fast" in deliv and shot in ["Scoop", "Sweep", "Pull", "Loft"]: wicket_weight *= 1.5
    elif "Outswing" in deliv and shot in ["Drive", "Cut"]: wicket_weight *= 1.4; four_weight *= 1.2
    elif "Inswing" in deliv and shot in ["Drive", "Flick", "Sweep"]: wicket_weight *= 1.4
        
    # 🚨 ANTI-OVERCOOK SAFETIES (Prevents stacked conditions from breaking the game)
    four_weight = max(0.5, min(four_weight, 25.0)) # Hard cap to prevent 450+ scores
    six_weight = max(0.1, min(six_weight, 15.0))
    wicket_weight = max(1.0, min(wicket_weight, 25.0)) # Hard cap to prevent 10/10 scenarios
    dot_weight = max(15.0, min(dot_weight, 120.0))

    weights = [dot_weight, single_weight, single_weight * 0.4, single_weight * 0.05, four_weight, six_weight, wicket_weight]
    outcome = random.choices(["dot", "single", "two", "three", "four", "six", "wicket"], weights=weights)[0]
    
    if is_no_ball and outcome == "wicket":
        outcome = "dot"
        prefix += "*(Wicket denied due to No Ball)*\n"
    if free_hit_active and not is_no_ball and outcome == "wicket":
        outcome = random.choice(["dot", "single", "two"])
        prefix += "🛡️ **FREE HIT!** Batter escapes dismissal!\n"
        
    b_stats.balls_faced += 1
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
        innings.over_log.append("<a:wickett:1510369641959264429>")
        match.prev_striker_idx = innings.current_striker_idx
        if dismissal_type in ["LBW", "Caught Behind"] and match.simulation_mode == "interactive":
            match.pending_drs = True
            match.drs_dismissal = dismissal_type
            
        max_wickets = 2 if getattr(match, "is_super_over", False) else 10
        if innings.wickets < max_wickets:
            is_ai_batting = match.is_ai_game and match.get_striker_user_id() == match.p2_id
            if match.simulation_mode == "whole_match" or is_ai_batting:
                innings.current_striker_idx = innings.next_batter_idx
                innings.next_batter_idx += 1
            else:
                match.pending_next_batter = True
                match.out_batter_profile = striker
        outcome_text = f"WICKET! ({dismissal_type.upper()})"
    else:
        runs_map = {"dot": 0, "single": 1, "two": 2, "three": 3, "four": 4, "six": 6}
        runs = runs_map[outcome]
        
        is_bye = False
        if runs == 0 and random.random() < 0.03:
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
                
            emoji_map = {0: "<a:0run:1510601371483897896>", 1: "<a:1run:1510600760570679356>", 2: "<a:2runs:1510601044818788403>", 3: "<a:3runs:1510600945053073508>", 4: "<a:4runs:1510600613556125787>", 6: "<a:6runs:1510600650613063761>"}
            log_entry = emoji_map[runs]
            
        if is_no_ball:
            log_entry = "NB" + (log_entry if runs > 0 and not is_bye else "")
            outcome_text += " (NO BALL)"
            
        if runs in [4, 6] and not is_bye:
            innings.last_ball_boundary = True
            if runs == 4:
                b_stats.fours = getattr(b_stats, 'fours', 0) + 1
            elif runs == 6:
                b_stats.sixes = getattr(b_stats, 'sixes', 0) + 1
            
        innings.over_log.append(log_entry)
        if runs in [1, 3]: innings.current_striker_idx, innings.current_non_striker_idx = innings.current_non_striker_idx, innings.current_striker_idx

    if not is_no_ball:
        bow_stats.balls_bowled += 1
        innings.total_balls += 1
        match.free_hit = False
        if innings.total_balls % 6 == 0:
            match.over_completed = True
            
    match.last_commentary = prefix + f"**{bowler['name']}** bowled a **{deliv}**\n**{striker['name']}** played: **{shot}**\n💥 **Result:** {outcome_text}"