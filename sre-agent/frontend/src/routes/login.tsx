import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useApp } from "@/lib/store";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  PRODUCT_NAME,
  TAGLINE_EN,
  TAGLINE_FR,
  BRAND_FOOTER_EN,
  BRAND_FOOTER_FR,
} from "@/lib/branding";

// Paste-the-token screen. Verifies by calling /api/mode; on 401 the API
// client clears the store and the user lands back here.
export default function LoginPage() {
  const { t } = useTranslation();
  const setToken = useApp((s) => s.setToken);
  const navigate = useNavigate();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError(null);
    setBusy(true);
    const tok = value.replace(/^SHP_API_TOKEN=/, "").trim();
    setToken(tok);
    try {
      await api.getMode();
      navigate("/cluster", { replace: true });
    } catch {
      setError(t("login.invalid"));
      setToken(null);
    } finally {
      setBusy(false);
    }
  }

  const locale = useApp((s) => s.locale);
  const tagline = locale === "fr" ? TAGLINE_FR : TAGLINE_EN;
  const footer = locale === "fr" ? BRAND_FOOTER_FR : BRAND_FOOTER_EN;

  return (
    <div className="grid min-h-screen place-items-center bg-background p-6">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center">
          <h1 className="text-4xl font-bold tracking-tight">{PRODUCT_NAME}</h1>
          <p className="mt-2 text-xs leading-snug text-muted-foreground">{tagline}</p>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>{t("login.title")}</CardTitle>
            <CardDescription>{t("login.body")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="tok">SHP_API_TOKEN</Label>
              <Input
                id="tok"
                type="password"
                placeholder={t("login.placeholder")}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void submit();
                }}
              />
            </div>
            {error ? <p className="text-sm text-danger">{error}</p> : null}
            <Button onClick={() => void submit()} disabled={busy || !value} className="w-full">
              {t("login.submit")}
            </Button>
          </CardContent>
        </Card>
        <p className="text-center text-[10px] uppercase tracking-wider text-muted-foreground">
          {footer}
        </p>
      </div>
    </div>
  );
}
