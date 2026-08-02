/**
 * Enforces the determinism rules that context.md §6 Stage 6 requires.
 *
 * Wall-clock and randomness are banned outright in TSX: every animation must be
 * a pure function of useCurrentFrame(). Documenting this ban was not enough —
 * it is easy to reach for Date.now() while animating, and the resulting
 * nondeterminism is invisible until two runs are compared frame by frame.
 */
module.exports = {
  root: true,
  parser: '@typescript-eslint/parser',
  parserOptions: {ecmaVersion: 2022, sourceType: 'module', ecmaFeatures: {jsx: true}},
  env: {browser: true, es2022: true},
  rules: {
    'no-restricted-globals': [
      'error',
      {name: 'Date', message: 'Wall-clock is banned in templates: animate from useCurrentFrame().'},
      {name: 'performance', message: 'performance.now() is banned: animate from useCurrentFrame().'},
    ],
    'no-restricted-syntax': [
      'error',
      {
        selector: "CallExpression[callee.object.name='Math'][callee.property.name='random']",
        message: 'Math.random() breaks determinism (R3). Derive variation from the frame or scene_id.',
      },
      {
        selector: "CallExpression[callee.object.name='Date'][callee.property.name='now']",
        message: 'Date.now() breaks determinism (R3). Animate from useCurrentFrame().',
      },
      {
        selector: "NewExpression[callee.name='Date']",
        message: 'new Date() breaks determinism (R3). Animate from useCurrentFrame().',
      },
    ],
  },
};
