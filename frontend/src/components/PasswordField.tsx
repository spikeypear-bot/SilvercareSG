import { useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { TextField, type TextFieldProps } from './TextField'

type PasswordFieldProps = Omit<TextFieldProps, 'type' | 'endAdornment'>

export function PasswordField(props: PasswordFieldProps) {
  const [visible, setVisible] = useState(false)
  const toggleLabel = `${visible ? 'Hide' : 'Show'} ${props.label.toLowerCase()}`

  return (
    <TextField
      {...props}
      type={visible ? 'text' : 'password'}
      autoCapitalize="none"
      autoCorrect="off"
      spellCheck={false}
      endAdornment={
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={toggleLabel}
          aria-pressed={visible}
          className="inline-flex size-11 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40"
        >
          {visible ? (
            <EyeOff className="size-5" aria-hidden="true" />
          ) : (
            <Eye className="size-5" aria-hidden="true" />
          )}
        </button>
      }
    />
  )
}
