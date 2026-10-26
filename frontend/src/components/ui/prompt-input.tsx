/*! @license
MIT License

Copyright (c) 2025 Julien Thibeaut

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE. app/page.tsx
*/
/**
 * Adapted from Prompt Kit's Prompt Input, published on 21st.dev by Julien Thibeaut.
 * Copyright (c) 2025 Julien Thibeaut. MIT; see LICENSES/prompt-kit.txt.
 * Uses native textarea/CSS and delegates keyboard submission to the caller.
 */
import { createContext, forwardRef, useContext, useLayoutEffect, useRef, useState } from 'react';
import type { ComponentProps, RefObject } from 'react';

type PromptInputContextValue = {
  value: string;
  setValue: (value: string) => void;
  maxHeight: number | string;
  disabled: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
};

const PromptInputContext = createContext<PromptInputContextValue | null>(null);

export type PromptInputProps = ComponentProps<'div'> & {
  value?: string;
  onValueChange?: (value: string) => void;
  maxHeight?: number | string;
  isLoading?: boolean;
  disabled?: boolean;
};

export function PromptInput({
  value,
  onValueChange,
  maxHeight = 240,
  isLoading = false,
  disabled = false,
  children,
  className,
  onClick,
  ...props
}: PromptInputProps) {
  const [internalValue, setInternalValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function setValue(next: string) {
    setInternalValue(next);
    onValueChange?.(next);
  }

  return <PromptInputContext.Provider value={{ value: value ?? internalValue, setValue, maxHeight, disabled, textareaRef }}>
    <div
      data-slot="prompt-input"
      data-disabled={disabled}
      aria-busy={isLoading || undefined}
      aria-disabled={disabled || undefined}
      className={className ? `ui-prompt-input ${className}` : 'ui-prompt-input'}
      onClick={event => {
        onClick?.(event);
        if (disabled || event.defaultPrevented) return;
        const target = event.target;
        if (target instanceof Element && target.closest('button, a, input, select, [role="button"], [contenteditable="true"]')) return;
        textareaRef.current?.focus();
      }}
      {...props}
    >{children}</div>
  </PromptInputContext.Provider>;
}

export type PromptInputTextareaProps = ComponentProps<'textarea'> & { disableAutosize?: boolean };

export const PromptInputTextarea = forwardRef<HTMLTextAreaElement, PromptInputTextareaProps>(function PromptInputTextarea({
  className,
  disableAutosize = false,
  value: ownValue,
  onChange,
  style,
  disabled,
  rows = 1,
  ...props
}, forwardedRef) {
  const context = useContext(PromptInputContext);
  if (!context) throw new Error('PromptInputTextarea must be rendered inside PromptInput.');
  const { value, setValue, maxHeight, textareaRef } = context;
  const renderedValue = ownValue ?? value;

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea || disableAutosize) return;

    const adjustHeight = () => {
      textarea.style.height = 'auto';
      textarea.style.height = typeof maxHeight === 'number'
        ? `${Math.min(textarea.scrollHeight, Math.max(maxHeight, 44))}px`
        : `min(${textarea.scrollHeight}px, ${maxHeight})`;
    };
    adjustHeight();

    let previousWidth = textarea.getBoundingClientRect().width;
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width;
      if (width === undefined || width === previousWidth) return;
      previousWidth = width;
      adjustHeight();
    });
    observer?.observe(textarea);
    return () => observer?.disconnect();
  }, [renderedValue, maxHeight, disableAutosize, textareaRef]);

  return <textarea
    ref={node => {
      textareaRef.current = node;
      if (typeof forwardedRef === 'function') forwardedRef(node);
      else if (forwardedRef) forwardedRef.current = node;
    }}
    data-slot="prompt-input-textarea"
    className={className ? `ui-prompt-textarea ${className}` : 'ui-prompt-textarea'}
    rows={rows}
    value={renderedValue}
    disabled={context.disabled || disabled}
    onChange={event => {
      setValue(event.target.value);
      onChange?.(event);
    }}
    style={{ maxHeight, ...style }}
    {...props}
  />;
});

export function PromptInputActions({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="prompt-input-actions" className={className ? `ui-prompt-actions ${className}` : 'ui-prompt-actions'} {...props} />;
}
