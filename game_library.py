#!/usr/bin/env python3
"""
Game Block Library
==================
Pre-coded JS feature blocks + HTML game templates for the pipeline.
Claude assembles these instead of writing from scratch → much faster builds.

NAMING CONTRACT (all blocks and templates follow this so they interoperate):
  player     : {x, y, w, h, vx, vy, onGround, hp, speed, facing}
  enemies[]  : [{x, y, w, h, vx, vy, hp, active, type, speed}]
  bullets[]  : [{x, y, vx, vy, w, h, active}]
  particles[]: [{x, y, vx, vy, life, maxLife, color, r}]
  platforms[]: [{x, y, w, h}]  (platformer template)
  obstacles[]: [{x, y, w, h}]  (runner template)
  canvas / ctx / W / H / keys / score / hiScore / gameOver / frame  (always present)
"""

# ─────────────────────────────────────────────────────────────────────────────
# FEATURE BLOCKS  — self-contained JS snippets Claude drops in and calls
# ─────────────────────────────────────────────────────────────────────────────

FEATURE_BLOCKS = {

# ── MOVEMENT ──────────────────────────────────────────────────────────────────

"move_4dir": """\
// BLOCK: move_4dir — 4-directional top-down movement (WASD / arrow keys)
// Call: move4dir(player) every frame.
function move4dir(p) {
  p.vx = 0; p.vy = 0;
  if (keys['ArrowLeft']  || keys['a']) p.vx = -p.speed;
  if (keys['ArrowRight'] || keys['d']) p.vx =  p.speed;
  if (keys['ArrowUp']    || keys['w']) p.vy = -p.speed;
  if (keys['ArrowDown']  || keys['s']) p.vy =  p.speed;
  if (p.vx && p.vy) { p.vx *= 0.707; p.vy *= 0.707; }  // normalise diagonal
  p.x += p.vx; p.y += p.vy;
  if (p.vx > 0) p.facing = 1; else if (p.vx < 0) p.facing = -1;
}""",

"move_sidescroll": """\
// BLOCK: move_sidescroll — left/right only (platformer / runner player)
// Call: moveSideScroll(player) every frame.
function moveSideScroll(p) {
  p.vx = 0;
  if (keys['ArrowLeft']  || keys['a']) p.vx = -p.speed;
  if (keys['ArrowRight'] || keys['d']) p.vx =  p.speed;
  p.x = Math.max(0, Math.min(W - p.w, p.x + p.vx));
  if (p.vx > 0) p.facing = 1; else if (p.vx < 0) p.facing = -1;
}""",

"move_gravity_jump": """\
// BLOCK: move_gravity_jump — gravity + jump (Space / ↑ / W)
// Call: applyGravityJump(player) every frame, AFTER moveSideScroll.
const GRAVITY = 0.55, JUMP_FORCE = -13, MAX_FALL = 14;
let _jumpPressed = false;
function applyGravityJump(p) {
  const wantJump = keys[' '] || keys['ArrowUp'] || keys['w'];
  if (wantJump && !_jumpPressed && p.onGround) { p.vy = JUMP_FORCE; p.onGround = false; }
  _jumpPressed = wantJump;
  if (!p.onGround) p.vy = Math.min(p.vy + GRAVITY, MAX_FALL);
  p.y += p.vy;
}""",

"move_screen_bounds": """\
// BLOCK: move_screen_bounds — clamp entity inside canvas
// Call: clampToBounds(player) every frame.
function clampToBounds(e) {
  e.x = Math.max(0, Math.min(W - e.w, e.x));
  e.y = Math.max(0, Math.min(H - e.h, e.y));
}""",

"move_screen_wrap": """\
// BLOCK: move_screen_wrap — entity wraps around canvas edges (asteroids-style)
// Call: wrapScreen(entity) every frame.
function wrapScreen(e) {
  if (e.x + e.w < 0) e.x = W;   else if (e.x > W) e.x = -e.w;
  if (e.y + e.h < 0) e.y = H;   else if (e.y > H) e.y = -e.h;
}""",

# ── COLLISION ─────────────────────────────────────────────────────────────────

"collision_rect": """\
// BLOCK: collision_rect — AABB rectangle overlap test
// Usage: if (rectHit(a, b)) { ... }   both need {x, y, w, h}
function rectHit(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x &&
         a.y < b.y + b.h && a.y + a.h > b.y;
}""",

"collision_circle": """\
// BLOCK: collision_circle — circle overlap test
// Usage: if (circleHit(a, b)) { ... }  both need {x, y, r} (centre + radius)
function circleHit(a, b) {
  const dx = (a.x + a.r) - (b.x + b.r);
  const dy = (a.y + a.r) - (b.y + b.r);
  return Math.sqrt(dx*dx + dy*dy) < a.r + b.r;
}""",

"collision_platform": """\
// BLOCK: collision_platform — land on top of platforms
// Call: landOnPlatforms(player, platforms) every frame AFTER gravity.
function landOnPlatforms(p, plats) {
  p.onGround = false;
  for (const pl of plats) {
    if (p.x + p.w > pl.x && p.x < pl.x + pl.w &&
        p.y + p.h >= pl.y && p.y + p.h <= pl.y + pl.h + Math.abs(p.vy) + 2 && p.vy >= 0) {
      p.y = pl.y - p.h; p.vy = 0; p.onGround = true;
    }
  }
  if (p.y + p.h >= H) { p.y = H - p.h; p.vy = 0; p.onGround = true; }  // floor
}""",

# ── COMBAT ─────────────────────────────────────────────────────────────────────

"combat_bullets": """\
// BLOCK: combat_bullets — shoot bullets in a direction (Space to fire)
// Call: tryShoot(fromX, fromY, dirX, dirY) on input.
//       updateBullets() + drawBullets() every frame.
const bullets = [];
const BULLET_SPEED = 9, BULLET_CD = 12;
let _shootCD = 0;
function tryShoot(fx, fy, dx, dy) {
  if (_shootCD > 0) return;
  const m = Math.sqrt(dx*dx + dy*dy) || 1;
  bullets.push({ x: fx, y: fy, vx: (dx/m)*BULLET_SPEED, vy: (dy/m)*BULLET_SPEED, w: 8, h: 8, active: true });
  _shootCD = BULLET_CD;
}
function updateBullets() {
  if (_shootCD > 0) _shootCD--;
  for (const b of bullets) { b.x += b.vx; b.y += b.vy; if (b.x<-20||b.x>W+20||b.y<-20||b.y>H+20) b.active=false; }
  bullets.splice(0, bullets.length, ...bullets.filter(b => b.active));
}
function drawBullets() {
  ctx.fillStyle = '#ff0';
  for (const b of bullets) { ctx.beginPath(); ctx.arc(b.x, b.y, 5, 0, Math.PI*2); ctx.fill(); }
}""",

"combat_bullet_hits": """\
// BLOCK: combat_bullet_hits — bullet vs enemy collision, removes both, adds score
// Requires: collision_rect, fx_particles, bullets[], enemies[]
// Call: checkBulletHits() every frame after updateBullets().
function checkBulletHits() {
  for (const b of bullets) {
    if (!b.active) continue;
    for (const e of enemies) {
      if (!e.active) continue;
      if (rectHit(b, e)) {
        b.active = false; e.hp = (e.hp||1) - 1;
        if (e.hp <= 0) { e.active = false; score += 10; spawnParticles(e.x+e.w/2, e.y+e.h/2, '#f80', 10); }
        break;
      }
    }
  }
  bullets.splice(0, bullets.length, ...bullets.filter(b => b.active));
  enemies.splice(0, enemies.length, ...enemies.filter(e => e.active));
}""",

# ── ENEMIES ────────────────────────────────────────────────────────────────────

"enemy_patrol": """\
// BLOCK: enemy_patrol — enemies pace left/right (or up/down)
// Add enemies with type:'patrol', startX, range, vx set.
// Call: updatePatrolEnemies() every frame.
function updatePatrolEnemies() {
  for (const e of enemies) {
    if (e.type !== 'patrol') continue;
    e.x += e.vx;
    if (e.x <= e.startX - e.range || e.x >= e.startX + e.range) e.vx *= -1;
  }
}""",

"enemy_chase": """\
// BLOCK: enemy_chase — enemies home toward player
// Add enemies with type:'chase', speed set.
// Call: updateChaseEnemies(player) every frame.
function updateChaseEnemies(p) {
  for (const e of enemies) {
    if (e.type !== 'chase') continue;
    const dx = (p.x+p.w/2)-(e.x+e.w/2), dy = (p.y+p.h/2)-(e.y+e.h/2);
    const m = Math.sqrt(dx*dx+dy*dy)||1;
    e.x += (dx/m)*e.speed; e.y += (dy/m)*e.speed;
  }
}""",

"enemy_edge_spawner": """\
// BLOCK: enemy_edge_spawner — spawn chase enemies from random screen edges
// Call: tickEdgeSpawner() every frame. Difficulty increases with score.
let _espTimer = 0, _espInterval = 90;
function tickEdgeSpawner() {
  _espTimer++;
  if (_espTimer < _espInterval) return;
  _espTimer = 0; _espInterval = Math.max(28, _espInterval - 0.8);
  const side = Math.floor(Math.random()*4);
  const x = side===1?W+10 : side===3?-40 : Math.random()*W;
  const y = side===0?-40  : side===2?H+10 : Math.random()*H;
  enemies.push({ x, y, w:30, h:30, hp:1, active:true, type:'chase', speed:1.4+score/600 });
}""",

"enemy_wave_spawner": """\
// BLOCK: enemy_wave_spawner — structured enemy waves with pause between waves
// Call: tickWaveSpawner() every frame. drawWaveLabel() in HUD.
let wave=1, _waveLeft=0, _wavePause=0;
function tickWaveSpawner() {
  if (_wavePause > 0) { _wavePause--; return; }
  if (enemies.length===0 && _waveLeft===0) { wave++; _waveLeft=3+wave*2; _wavePause=120; }
  if (_waveLeft > 0 && frame%40===0) {
    _waveLeft--;
    const side=Math.floor(Math.random()*4);
    const x=side===1?W+10:side===3?-40:Math.random()*W;
    const y=side===0?-40:side===2?H+10:Math.random()*H;
    enemies.push({x,y,w:30,h:30,hp:1+Math.floor(wave/3),active:true,type:'chase',speed:1+wave*0.15});
  }
}
function drawWaveLabel() {
  ctx.fillStyle='#888'; ctx.font='13px monospace';
  ctx.fillText('Wave '+wave, W/2-25, 22);
}""",

"enemy_runner_obstacles": """\
// BLOCK: enemy_runner_obstacles — scrolling right-to-left obstacles for runner games
// Uses obstacles[] array (separate from enemies[]).
// Call: tickObstacles() + drawObstacles() every frame.
const obstacles = [];
let _obTimer=0, _obInterval=80, scrollSpeed=4;
function tickObstacles() {
  _obTimer++;
  if (_obTimer >= _obInterval) {
    _obTimer=0; _obInterval=Math.max(36,_obInterval-0.4);
    obstacles.push({ x:W+10, y:H-80, w:22+Math.random()*20, h:40+Math.random()*40 });
  }
  for (const o of obstacles) o.x -= scrollSpeed;
  obstacles.splice(0,obstacles.length,...obstacles.filter(o=>o.x+o.w>-10));
  scrollSpeed = Math.min(10, 4 + score/300);
}
function drawObstacles() {
  ctx.fillStyle='#2a5';
  for (const o of obstacles) ctx.fillRect(o.x,o.y,o.w,o.h);
}""",

# ── VISUAL EFFECTS ─────────────────────────────────────────────────────────────

"fx_particles": """\
// BLOCK: fx_particles — simple burst particle system
// Call: spawnParticles(x, y, color, count) to burst.
//       updateParticles() + drawParticles() every frame.
const particles = [];
function spawnParticles(x,y,color,count=8) {
  for (let i=0;i<count;i++) {
    const a=Math.random()*Math.PI*2, s=1.5+Math.random()*3;
    particles.push({x,y,vx:Math.cos(a)*s,vy:Math.sin(a)*s,life:30+Math.random()*20,maxLife:50,color,r:2+Math.random()*3});
  }
}
function updateParticles() {
  for (const p of particles){p.x+=p.vx;p.y+=p.vy;p.vx*=0.94;p.vy*=0.94;p.life--;}
  particles.splice(0,particles.length,...particles.filter(p=>p.life>0));
}
function drawParticles() {
  for (const p of particles){
    ctx.globalAlpha=p.life/p.maxLife; ctx.fillStyle=p.color;
    ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2); ctx.fill();
  }
  ctx.globalAlpha=1;
}""",

"fx_screen_shake": """\
// BLOCK: fx_screen_shake — camera shake on impact
// Call: shakeScreen(intensity) to trigger.
//       applyShake() at start of draw(), resetShake() at end.
let _shakeAmt=0;
function shakeScreen(intensity=8){_shakeAmt=intensity;}
function applyShake(){
  if(_shakeAmt>0.5){
    ctx.save();ctx.translate((Math.random()-.5)*_shakeAmt,(Math.random()-.5)*_shakeAmt);
    _shakeAmt*=0.78;
  }
}
function resetShake(){if(_shakeAmt>0.5)ctx.restore();}""",

# ── UI / GAME STATE ────────────────────────────────────────────────────────────

"ui_lives": """\
// BLOCK: ui_lives — 3-lives system with invincibility frames after hit
// Call: initLives() in reset(). loseLife() on damage. tickInvincible() + drawLives() each frame.
let lives=3, _invinc=0;
function initLives(){lives=3;_invinc=0;}
function loseLife(){if(_invinc>0)return; lives--; _invinc=120; shakeScreen(10); if(lives<=0)gameOver=true;}
function tickInvincible(){if(_invinc>0)_invinc--;}
function isInvincible(){return _invinc>0;}
function drawLives(){
  ctx.font='18px monospace';
  for(let i=0;i<lives;i++){ctx.fillStyle='#f44';ctx.fillText('♥',12+i*22,H-10);}
}""",

"ui_timer": """\
// BLOCK: ui_timer — countdown timer (game ends at 0)
// Call: initTimer(seconds) in reset(). tickTimer() + drawTimer() each frame.
let timeLeft=60, _timerTick=0;
function initTimer(s=60){timeLeft=s;_timerTick=0;}
function tickTimer(){_timerTick++;if(_timerTick>=60){_timerTick=0;timeLeft=Math.max(0,timeLeft-1);}if(timeLeft<=0)gameOver=true;}
function drawTimer(){
  ctx.fillStyle=timeLeft<=10?'#f44':'#fff';
  ctx.font='bold 18px monospace';
  ctx.fillText('⏱ '+timeLeft+'s',W/2-32,26);
}""",

"ui_combo": """\
// BLOCK: ui_combo — score combo multiplier (resets if no kill within window)
// Call: addComboKill() on each kill. tickCombo() + drawCombo() each frame.
let combo=0, comboMult=1, _comboTimer=0;
const COMBO_WINDOW=110;
function addComboKill(){combo++;comboMult=Math.min(8,1+Math.floor(combo/3));_comboTimer=COMBO_WINDOW;}
function tickCombo(){if(_comboTimer>0)_comboTimer--;else{combo=0;comboMult=1;}}
function drawCombo(){
  if(combo>=2){ctx.fillStyle='#f80';ctx.font='bold 15px monospace';ctx.fillText('x'+comboMult+' COMBO',W-118,50);}
}""",

"ui_powerups": """\
// BLOCK: ui_powerups — spawns collectible power-ups; speed boost + score bonus
// Call: tickPowerups() + drawPowerups() each frame.
const powerups=[];
let _activePU=null,_puTimer=0,_puSpawn=0;
const PU_TYPES=[
  {type:'speed',color:'#0ff',icon:'⚡',dur:300},
  {type:'score',color:'#ff0',icon:'★',dur:0},
];
function tickPowerups(){
  _puSpawn++;
  if(_puSpawn>600){_puSpawn=0;const t=PU_TYPES[Math.floor(Math.random()*PU_TYPES.length)];powerups.push({x:60+Math.random()*(W-120),y:60+Math.random()*(H-120),w:22,h:22,...t,collected:false});}
  for(const p of powerups){if(rectHit(player,p)&&!p.collected){p.collected=true;if(p.type==='score')score+=50;else{_activePU=p.type;_puTimer=p.dur;}}}
  powerups.splice(0,powerups.length,...powerups.filter(p=>!p.collected));
  if(_puTimer>0)_puTimer--;else _activePU=null;
}
function drawPowerups(){
  for(const p of powerups){ctx.font='20px monospace';ctx.fillStyle=p.color;ctx.fillText(p.icon,p.x,p.y+18);}
}
function hasPowerup(type){return _activePU===type&&_puTimer>0;}""",

}  # end FEATURE_BLOCKS


# ─────────────────────────────────────────────────────────────────────────────
# GAME TYPE PROFILES  — canonical template + recommended blocks per game type
# ─────────────────────────────────────────────────────────────────────────────

GAME_TYPE_PROFILES = {
    "runner": {
        "description": "Endless side-scroller — player jumps over obstacles, score = distance survived",
        "default_blocks": ["move_gravity_jump", "collision_rect", "enemy_runner_obstacles", "fx_particles", "fx_screen_shake"],
    },
    "topdown_shooter": {
        "description": "Top-down arena — WASD to move, Space to shoot, survive enemy waves",
        "default_blocks": ["move_4dir", "move_screen_bounds", "collision_rect", "combat_bullets",
                           "combat_bullet_hits", "enemy_edge_spawner", "enemy_wave_spawner",
                           "fx_particles", "fx_screen_shake"],
    },
    "platformer": {
        "description": "Side-scrolling platformer — jump on platforms, avoid patrolling enemies",
        "default_blocks": ["move_sidescroll", "move_gravity_jump", "collision_rect",
                           "collision_platform", "enemy_patrol", "fx_particles", "ui_lives"],
    },
    "dodge": {
        "description": "Dodge game — move to avoid incoming objects, survive as long as possible",
        "default_blocks": ["move_4dir", "move_screen_bounds", "collision_rect",
                           "enemy_edge_spawner", "fx_particles", "fx_screen_shake", "ui_lives"],
    },
    "snake": {
        "description": "Snake — steer to eat food, grow longer, avoid walls and yourself",
        "default_blocks": ["collision_rect"],
    },
    "breakout": {
        "description": "Breakout/arkanoid — bounce a ball to smash bricks with a paddle",
        "default_blocks": ["collision_rect", "fx_particles", "fx_screen_shake", "ui_lives"],
    },
    "stealth": {
        "description": "Stealth — sneak past detection zones and patrolling guards to reach the exit",
        "default_blocks": ["move_4dir", "move_screen_bounds", "collision_rect",
                           "enemy_patrol", "fx_particles"],
    },
    "generic": {
        "description": "General browser canvas game",
        "default_blocks": ["move_4dir", "move_screen_bounds", "collision_rect", "fx_particles"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# GAME TEMPLATES  — complete HTML skeletons, Claude fills in ~20% of the code
# ─────────────────────────────────────────────────────────────────────────────

def _html(title, W, H, ui_hint, extra_state=""):
    """Shared boilerplate — every template expands from this."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:#111;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:monospace;color:#fff;}}
  canvas{{border:2px solid #333;display:block;}}
  #ui{{margin-top:8px;font-size:13px;color:#888;}}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="ui">{ui_hint}</div>
<script>
const canvas=document.getElementById('c');
const ctx=canvas.getContext('2d');
const W=canvas.width={W}, H=canvas.height={H};

// ── Input ───────────────────────────────────────────────
const keys={{}};
window.addEventListener('keydown',e=>{{keys[e.key]=true; if([' ','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key))e.preventDefault();}});
window.addEventListener('keyup',e=>{{keys[e.key]=false;}});

// ── Core state ──────────────────────────────────────────
let score=0, hiScore=0, gameOver=false, frame=0;
const enemies=[];
{extra_state}

// ── HUD ─────────────────────────────────────────────────
function drawHUD(){{
  ctx.fillStyle='#fff'; ctx.font='bold 18px monospace';
  ctx.fillText('Score: '+score,12,26);
  ctx.fillText('Best: '+hiScore,W-150,26);
  // TODO: Claude adds extra HUD elements here if spec needs them
}}

// ── Game over screen ────────────────────────────────────
function drawGameOver(){{
  ctx.fillStyle='rgba(0,0,0,0.72)'; ctx.fillRect(0,0,W,H);
  ctx.textAlign='center';
  ctx.fillStyle='#f44'; ctx.font='bold 52px monospace'; ctx.fillText('GAME OVER',W/2,H/2-40);
  ctx.fillStyle='#fff'; ctx.font='22px monospace';    ctx.fillText('Score: '+score,W/2,H/2+10);
  ctx.fillStyle='#aaa'; ctx.font='16px monospace';    ctx.fillText('Press R to restart',W/2,H/2+50);
  ctx.textAlign='left';
  if(score>hiScore) hiScore=score;
}}

// ── Main loop ───────────────────────────────────────────
function loop(){{
  requestAnimationFrame(loop);
  if(keys['r']||keys['R']){{reset();return;}}
  if(!gameOver){{update();draw();drawHUD();frame++;}}
  else{{draw();drawGameOver();}}
}}

// ══ PASTE FEATURE BLOCKS HERE (Claude inserts them) ══════

// ══ GAME-SPECIFIC CODE ════════════════════════════════════

function reset(){{
  score=0; gameOver=false; frame=0;
  enemies.length=0;
  // TODO: reset extra arrays (obstacles, bullets, particles, etc.)
  initGame();
}}

function initGame(){{
  // TODO: initialise player, place enemies/platforms, set starting state
}}

function update(){{
  // TODO: call movement blocks, enemy update blocks, collision checks, spawners, effects
}}

function draw(){{
  ctx.fillStyle='#111'; ctx.fillRect(0,0,W,H);
  // TODO: draw background, player, enemies, particles, UI overlays
}}

reset();
loop();
</script>
</body>
</html>"""


GAME_TEMPLATES = {

    "runner": _html(
        "GAME_TITLE", 800, 400,
        "Space / ↑ to jump &nbsp;|&nbsp; R to restart",
        extra_state="let scrollSpeed=4, groundY=340;\nconst obstacles=[];",
    ),

    "topdown_shooter": _html(
        "GAME_TITLE", 800, 600,
        "WASD to move &nbsp;|&nbsp; Space to shoot &nbsp;|&nbsp; R to restart",
    ),

    "platformer": _html(
        "GAME_TITLE", 800, 500,
        "← → to move &nbsp;|&nbsp; Space / ↑ to jump &nbsp;|&nbsp; R to restart",
        extra_state="const platforms=[];\nconst bullets=[];",
    ),

    "dodge": _html(
        "GAME_TITLE", 600, 700,
        "WASD / arrow keys to dodge &nbsp;|&nbsp; R to restart",
    ),

    "snake": _html(
        "GAME_TITLE", 600, 600,
        "Arrow keys to steer &nbsp;|&nbsp; R to restart",
        extra_state=("const CELL=20;\n"
                     "let snake=[], snakeDir={x:1,y:0}, nextDir={x:1,y:0}, food={x:0,y:0}, _snakeTick=0;"),
    ),

    "breakout": _html(
        "GAME_TITLE", 600, 700,
        "← → to move paddle &nbsp;|&nbsp; Space to launch &nbsp;|&nbsp; R to restart",
        extra_state=("const bricks=[];\n"
                     "let ball={x:300,y:500,vx:3,vy:-4,r:8,launched:false};\n"
                     "let paddle={x:250,y:650,w:100,h:14,speed:6};"),
    ),

    "stealth": _html(
        "GAME_TITLE", 800, 600,
        "WASD to move &nbsp;|&nbsp; Shift to sneak &nbsp;|&nbsp; R to restart",
        extra_state="const detectionZones=[], exitZone={x:0,y:0,w:0,h:0};",
    ),

    "generic": _html(
        "GAME_TITLE", 800, 500,
        "Arrow keys / WASD to move &nbsp;|&nbsp; Space to action &nbsp;|&nbsp; R to restart",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# DETECT GAME PROFILE  (one GPT call after spec is written)
# ─────────────────────────────────────────────────────────────────────────────

def detect_game_profile(spec: str, history: list, client) -> tuple[str, list[str]]:
    """Ask GPT to classify the game type and pick which feature blocks are needed."""

    type_options = "\n".join(
        f"  {k}: {v['description']}" for k, v in GAME_TYPE_PROFILES.items()
    )
    block_options = ", ".join(FEATURE_BLOCKS.keys())

    msg = (
        f"Given this game spec, answer two questions.\n\nSPEC:\n{spec}\n\n"
        f"Q1 — Which game type fits best? Pick exactly one:\n{type_options}\n\n"
        f"Q2 — Which feature blocks does this game need? Pick from:\n{block_options}\n"
        "Only include blocks the spec actually requires. Skip anything irrelevant.\n\n"
        "Reply in EXACTLY this format (no other text):\n"
        "GAME_TYPE: <type>\n"
        "BLOCKS: <block1>, <block2>, <block3>"
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=history + [{"role": "user", "content": msg}],
    )
    raw = resp.choices[0].message.content.strip()

    game_type = "generic"
    blocks: list[str] = []

    for line in raw.splitlines():
        if line.startswith("GAME_TYPE:"):
            t = line.split(":", 1)[1].strip().lower()
            if t in GAME_TYPE_PROFILES:
                game_type = t
        elif line.startswith("BLOCKS:"):
            raw_blocks = line.split(":", 1)[1].strip()
            blocks = [b.strip() for b in raw_blocks.split(",") if b.strip() in FEATURE_BLOCKS]

    # Fall back to profile defaults if GPT returned nothing valid
    if not blocks:
        blocks = list(GAME_TYPE_PROFILES[game_type]["default_blocks"])

    return game_type, blocks


# ─────────────────────────────────────────────────────────────────────────────
# BUILD PROMPT WITH BLOCKS  (replaces build_initial_prompt in pipeline_v2)
# ─────────────────────────────────────────────────────────────────────────────

def build_prompt_with_blocks(spec: str, game_type: str, block_names: list[str],
                              complexity: str, title: str = "Game") -> str:
    """Assemble the Claude prompt: spec + selected blocks + matching template."""

    template = GAME_TEMPLATES.get(game_type, GAME_TEMPLATES["generic"])
    template = template.replace("GAME_TITLE", title)

    selected_blocks = "\n\n".join(
        FEATURE_BLOCKS[b] for b in block_names if b in FEATURE_BLOCKS
    )

    scope = {
        "simple": "Minimal — implement only what the spec requires. No extras. Just get the core loop working.",
        "medium": "Solid — implement the full spec with good collision detection and clear visuals.",
        "rich":   "Complete — all spec features, polished visuals, smooth feel, good game-juice.",
    }[complexity]

    profile = GAME_TYPE_PROFILES.get(game_type, GAME_TYPE_PROFILES["generic"])

    return (
        f"Complete this HTML5 canvas game by filling in initGame(), update(), and draw().\n\n"
        f"GAME SPEC:\n{spec}\n\n"
        f"SCOPE: {scope}\n\n"
        f"GAME TYPE: {game_type} — {profile['description']}\n\n"
        f"PRE-BUILT FEATURE BLOCKS — paste these into the <script> where it says "
        f"'PASTE FEATURE BLOCKS HERE', then call them in update() and draw():\n\n"
        f"{selected_blocks}\n\n"
        f"HTML TEMPLATE (save as index.html):\n{template}\n\n"
        "INSTRUCTIONS — read carefully:\n"
        "1. Paste ALL the feature blocks above into the script (replace the placeholder comment)\n"
        "2. Fill in initGame() — create the player object, place enemies, set initial positions\n"
        "3. Fill in update() — call the block functions in the right order: "
        "move → gravity → collision → enemies → spawners → effects → combo/lives ticks\n"
        "4. Fill in draw() — clear canvas, draw background, draw entities, call drawParticles()\n"
        "5. Do NOT rewrite the HUD, game-over screen, main loop, or input handler — they are done\n"
        "6. Player object MUST follow the contract: {x, y, w, h, vx, vy, onGround, hp, speed, facing}\n"
        "7. Save the file as index.html\n"
    )
