// Free CPU inference using Magenta's pretrained 13 MB MelodyRNN.
const fs = require('fs');
const tf = require('@tensorflow/tfjs');
const {MusicRNN} = require('@magenta/music/node/music_rnn');

async function main() {
  await tf.setBackend('cpu');
  await tf.ready();
  const model = new MusicRNN('https://storage.googleapis.com/magentadata/js/checkpoints/music_rnn/basic_rnn');
  await model.initialize();
  // Original A-minor seed; the renderer adds sparse horror textures.
  const pitches = [57, 60, 64, 59, 57, 65, 64, 60];
  const primer = {quantizationInfo: {stepsPerQuarter: 4}, totalQuantizedSteps: 32,
    notes: pitches.map((pitch, i) => ({pitch, quantizedStartStep: i * 4, quantizedEndStep: i * 4 + 3}))};
  const result = await model.continueSequence(primer, 256, 0.7);
  if (!result.notes || result.notes.length < 8) throw new Error('Model generated insufficient music');
  fs.writeFileSync(process.argv[2] || 'music-notes.json', JSON.stringify(result));
  model.dispose();
  console.log(JSON.stringify({check: 'Free CPU MusicRNN', passed: true, notes: result.notes.length}));
}
main().catch(error => {console.error(error); process.exit(1);});
