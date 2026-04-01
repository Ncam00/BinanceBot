const fs = require('fs');
const trades = JSON.parse(fs.readFileSync('data/trades.json'));
const sells = trades.filter(t => t.side === 'SELL' && !t.paper);
let wins = 0, losses = 0, neutral = 0, totalPnl = 0;
let winTotal = 0, lossTotal = 0;

sells.forEach(t => {
    if (t.pnl > 0) { wins++; winTotal += t.pnl; }
    else if (t.pnl < 0) { losses++; lossTotal += t.pnl; }
    else { neutral++; }
    if (t.pnl) totalPnl += t.pnl;
});

console.log('═══════════════════════════════════════');
console.log('  TRADE STATISTICS (LIVE only)');
console.log('═══════════════════════════════════════');
console.log(`  Total trades:   ${trades.filter(t => !t.paper).length}`);
console.log(`  BUY orders:     ${trades.filter(t => t.side === 'BUY' && !t.paper).length}`);
console.log(`  SELL orders:    ${sells.length}`);
console.log('───────────────────────────────────────');
console.log(`  Winners:        ${wins} ✅`);
console.log(`  Losers:         ${losses} ❌`);
console.log(`  Neutral:        ${neutral}`);
console.log(`  Win Rate:       ${((wins / (wins + losses)) * 100).toFixed(1)}%`);
console.log('───────────────────────────────────────');
console.log(`  Total P/L:      $${totalPnl.toFixed(2)}`);
console.log(`  Avg Win:        +$${(winTotal / wins).toFixed(2)}`);
console.log(`  Avg Loss:       -$${Math.abs(lossTotal / losses).toFixed(2)}`);
console.log(`  Biggest Win:    +$${Math.max(...sells.filter(t=>t.pnl>0).map(t=>t.pnl)).toFixed(2)}`);
console.log(`  Biggest Loss:   -$${Math.abs(Math.min(...sells.filter(t=>t.pnl<0).map(t=>t.pnl))).toFixed(2)}`);
console.log('═══════════════════════════════════════');
