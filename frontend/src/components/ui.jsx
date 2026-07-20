/**
 * Shared primitives.
 *
 * Deliberately one file and deliberately small. These five cover what actually
 * repeats across the console; anything used once stays inline in its component,
 * because a primitive with a single caller is just indirection. Every visual
 * value comes from a design token — none of these should ever contain a hex
 * colour or a `dark:` variant.
 */
import { forwardRef } from 'react'

export function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

/** A raised panel. `pad={false}` when the content manages its own padding
 *  (tables, scroll regions, anything that needs to bleed to the edge). */
export function Card({ as: Tag = 'div', className, pad = true, children, ...rest }) {
  return (
    <Tag
      className={cx(
        'rounded-lg border border-line bg-raised',
        pad && 'p-4',
        className,
      )}
      {...rest}
    >
      {children}
    </Tag>
  )
}

/** Section label. The console's structural voice — see .eyebrow in index.css. */
export function Eyebrow({ className, children, ...rest }) {
  return (
    <div className={cx('eyebrow', className)} {...rest}>
      {children}
    </div>
  )
}

const BUTTON_BASE =
  'inline-flex items-center justify-center gap-2 rounded-md border font-medium ' +
  'transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45'

/**
 * Two weights of accent, and the difference carries meaning:
 *
 *   FILLED (`primary`)  — the line has stopped and needs your decision.
 *                         Only gate actions and the resume/approve path.
 *   OUTLINE (`accent`)  — an important action that is available, not demanded.
 *                         "New project" and similar entry points.
 *
 * Everything the machine does unattended stays in neutrals. Without this split
 * the gold ends up on every call to action and stops meaning anything, which is
 * exactly what makes a waiting gate hard to spot in a long list.
 */
const BUTTON_VARIANTS = {
  primary:
    'border-accent bg-accent text-accent-ink hover:brightness-110 active:brightness-95',
  accent:
    'border-accent/45 bg-accent-soft text-accent hover:border-accent hover:bg-accent/15',
  secondary:
    'border-line-strong bg-overlay text-ink hover:border-ink-3 hover:bg-line',
  ghost: 'border-transparent bg-transparent text-ink-2 hover:bg-overlay hover:text-ink',
  danger: 'border-transparent bg-err text-white hover:brightness-110',
}

const BUTTON_SIZES = {
  sm: 'h-8 px-3 text-[13px]',
  md: 'h-9 px-4 text-sm',
  lg: 'h-11 px-5 text-sm',
}

export const Button = forwardRef(function Button(
  { variant = 'secondary', size = 'md', className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], className)}
      {...rest}
    >
      {children}
    </button>
  )
})

/**
 * Status pill. `tone` is a token name (run/ok/warn/err/idle/alt/accent).
 *
 * Filled is reserved for states that need the operator; everything else is a
 * tinted outline. That is the whole colour discipline of the app expressed in
 * one prop, which is why callers pass a tone rather than class names.
 */
const BADGE_TONES = {
  run: 'border-run/35 text-run bg-run/10',
  ok: 'border-ok/35 text-ok bg-ok/10',
  warn: 'border-warn/40 text-warn bg-warn/10',
  err: 'border-err/40 text-err bg-err/10',
  idle: 'border-line-strong text-ink-3 bg-overlay',
  alt: 'border-alt/35 text-alt bg-alt/10',
  accent: 'border-accent/50 text-accent bg-accent-soft',
}

export function Badge({ tone = 'idle', className, children, ...rest }) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded border px-1.5 py-0.5',
        'font-mono text-[10px] font-semibold uppercase tracking-wider',
        BADGE_TONES[tone] || BADGE_TONES.idle,
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  )
}

/** Small filled dot, for badges and timeline nodes. */
export function Dot({ tone = 'idle', className }) {
  const map = {
    run: 'bg-run', ok: 'bg-ok', warn: 'bg-warn', err: 'bg-err',
    idle: 'bg-idle', alt: 'bg-alt', accent: 'bg-accent',
  }
  return <span className={cx('h-1.5 w-1.5 shrink-0 rounded-full', map[tone] || map.idle, className)} />
}

/**
 * Loading placeholder.
 *
 * Shaped like the content it stands in for, so the layout does not jump when
 * the real thing lands. The pulse is a plain CSS animation, which the global
 * reduced-motion rule already flattens — no per-component check needed.
 */
export function Skeleton({ className, ...rest }) {
  return (
    <div
      aria-hidden="true"
      className={cx('animate-pulse rounded bg-overlay', className)}
      {...rest}
    />
  )
}

/** Several skeleton lines with a ragged last line, as prose actually wraps. */
export function SkeletonText({ lines = 3, className }) {
  return (
    <div className={cx('space-y-2', className)}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton
          key={i}
          className="h-3"
          style={{ width: i === lines - 1 ? '62%' : `${88 + ((i * 7) % 12)}%` }}
        />
      ))}
    </div>
  )
}
