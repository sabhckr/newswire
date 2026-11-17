"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";

import { Button } from "./button";
import { Dialog, DialogContent } from "./dialog";

type ConfirmOptions = {
  title: string;
  body?: React.ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
};

const Ctx = createContext<(o: ConfirmOptions) => Promise<boolean>>(async () => false);

/** `const confirm = useConfirm(); if (await confirm({...})) ...` for every destructive action. */
export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<(v: boolean) => void>(undefined);
  const confirm = useCallback((o: ConfirmOptions) => {
    setOpts(o);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);
  const close = (v: boolean) => {
    resolver.current?.(v);
    setOpts(null);
  };
  return (
    <Ctx.Provider value={confirm}>
      {children}
      <Dialog open={!!opts} onOpenChange={(o) => !o && close(false)}>
        {opts ? (
          <DialogContent
            title={opts.title}
            footer={
              <>
                <Button variant="outline" onClick={() => close(false)}>
                  Cancel
                </Button>
                <Button variant={opts.destructive ? "danger" : "primary"} onClick={() => close(true)} autoFocus>
                  {opts.confirmLabel ?? "Confirm"}
                </Button>
              </>
            }
          >
            <div className="text-sm text-muted">{opts.body}</div>
          </DialogContent>
        ) : null}
      </Dialog>
    </Ctx.Provider>
  );
}

export function useConfirm() {
  return useContext(Ctx);
}
