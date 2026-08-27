import {
  CheckCircle2,
  ChevronDown,
  KeyRound,
  LoaderCircle,
  PhoneCall,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  getCountries,
  getCountryCallingCode,
  parsePhoneNumberFromString,
  type CountryCode,
} from "libphonenumber-js/min";
import { type FormEvent, useEffect, useState } from "react";

import type { PhoneVerificationStarted } from "../types";

interface OutboundCallPanelProps {
  open: boolean;
  submitting: boolean;
  verificationAvailable: boolean;
  verificationRequired: boolean;
  manualVerificationRequired: boolean;
  onClose: () => void;
  onSubmit: (phoneNumber: string, customerName: string) => Promise<void>;
  onStartVerification: (phoneNumber: string) => Promise<PhoneVerificationStarted>;
  onCheckVerification: (phoneNumber: string) => Promise<boolean>;
}

const regionNames = new Intl.DisplayNames(["en"], { type: "region" });

function countryFlag(country: CountryCode): string {
  return Array.from(country)
    .map((letter) => String.fromCodePoint(letter.charCodeAt(0) + 127397))
    .join("");
}

const countryOptions = getCountries()
  .map((country) => ({
    country,
    name: regionNames.of(country) || country,
    callingCode: `+${getCountryCallingCode(country)}`,
  }))
  .sort((left, right) => left.name.localeCompare(right.name));

function normalizePhoneNumber(value: string, country: CountryCode): string | null {
  const parsed = parsePhoneNumberFromString(value.trim(), country);
  return parsed?.isPossible() ? parsed.number : null;
}

export function OutboundCallPanel({
  open,
  submitting,
  verificationAvailable,
  verificationRequired,
  manualVerificationRequired,
  onClose,
  onSubmit,
  onStartVerification,
  onCheckVerification,
}: OutboundCallPanelProps) {
  const [country, setCountry] = useState<CountryCode>("IN");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [verificationCode, setVerificationCode] = useState<string | null>(null);
  const [verifiedNumber, setVerifiedNumber] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [checkingVerification, setCheckingVerification] = useState(false);
  const busy = submitting || verifying || checkingVerification;
  const normalizedPhoneNumber = normalizePhoneNumber(phoneNumber, country);
  const currentNumberVerified = Boolean(
    normalizedPhoneNumber && verifiedNumber === normalizedPhoneNumber,
  );

  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [busy, onClose, open]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalized = validateNumber();
    if (!normalized) return;
    if (verificationRequired && verifiedNumber !== normalized) {
      setError("Verify this number before starting a trial call.");
      return;
    }
    setError(null);
    try {
      await onSubmit(normalized, customerName.trim() || "there");
      setPhoneNumber("");
      setCustomerName("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The call could not be started.");
    }
  };

  const validateNumber = (): string | null => {
    const normalized = normalizePhoneNumber(phoneNumber, country);
    if (!normalized) {
      setError("Enter a valid phone number for the selected country.");
      return null;
    }
    return normalized;
  };

  const startVerification = async () => {
    const normalized = validateNumber();
    if (!normalized) return;
    setError(null);
    setVerifying(true);
    try {
      const result = await onStartVerification(normalized);
      if (result.status === "verified") {
        setVerifiedNumber(normalized);
        setVerificationCode(null);
      } else {
        setVerificationCode(result.validation_code);
        setVerifiedNumber(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Twilio could not start verification.");
    } finally {
      setVerifying(false);
    }
  };

  const checkVerification = async () => {
    const normalized = validateNumber();
    if (!normalized) return;
    setError(null);
    setCheckingVerification(true);
    try {
      const verified = await onCheckVerification(normalized);
      if (verified) {
        setVerifiedNumber(normalized);
        setVerificationCode(null);
      } else {
        setError("Twilio has not verified the number yet. Enter the code during its call, then check again.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Verification status could not be checked.");
    } finally {
      setCheckingVerification(false);
    }
  };

  return (
    <>
      <button
        className={`call-scrim ${open ? "is-open" : ""}`}
        type="button"
        aria-label="Close phone call panel"
        onClick={() => !busy && onClose()}
      />
      <section
        className={`outbound-panel ${open ? "is-open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="outbound-title"
        aria-hidden={!open}
      >
        <div className="outbound-panel-header">
          <span className="outbound-icon"><PhoneCall size={21} /></span>
          <div>
            <span className="eyebrow">Outbound voice</span>
            <h2 id="outbound-title">Call a phone</h2>
          </div>
          <button className="icon-button" type="button" aria-label="Close" disabled={busy} onClick={onClose}>
            <X size={19} />
          </button>
        </div>

        <form className="outbound-form" onSubmit={(event) => void submit(event)}>
          <label className="field">
            Phone number
            <div className="phone-number-control">
              <div className="country-code-select">
                <select
                  aria-label="Country and calling code"
                  value={country}
                  disabled={busy}
                  onChange={(event) => {
                    setCountry(event.target.value as CountryCode);
                    setError(null);
                    setVerificationCode(null);
                    setVerifiedNumber(null);
                  }}
                >
                  {countryOptions.map((option) => (
                    <option key={option.country} value={option.country}>
                      {countryFlag(option.country)} {option.name} ({option.callingCode})
                    </option>
                  ))}
                </select>
                <ChevronDown size={15} aria-hidden="true" />
              </div>
              <input
                type="tel"
                inputMode="tel"
                autoComplete="tel-national"
                placeholder="98765 43210"
                value={phoneNumber}
                disabled={busy}
                onChange={(event) => {
                  setError(null);
                  setPhoneNumber(event.target.value);
                  setVerificationCode(null);
                  setVerifiedNumber(null);
                }}
                autoFocus={open}
              />
            </div>
            {normalizedPhoneNumber && (
              <small className="phone-number-preview">Calling {normalizedPhoneNumber}</small>
            )}
            <small className="field-hint">
              {manualVerificationRequired
                ? "Trial calls work only with recipients verified in Twilio Console."
                : verificationRequired
                  ? "This call requires number verification."
                  : "Choose a country and enter the local phone number."}
            </small>
          </label>

          <label className="field">
            Caller name <small>Optional</small>
            <input
              type="text"
              maxLength={120}
              placeholder="Guest name"
              value={customerName}
              disabled={busy}
              onChange={(event) => setCustomerName(event.target.value)}
            />
          </label>

          {error && <p className="outbound-error" role="alert">{error}</p>}

          {verificationCode && !currentNumberVerified && (
            <div className="verification-card" role="status">
              <span><KeyRound size={15} /> Twilio validation code</span>
              <strong>{verificationCode}</strong>
              <p>Answer Twilio’s verification call and enter this code using the phone keypad.</p>
              <button
                className="secondary-button verification-check"
                type="button"
                disabled={busy}
                onClick={() => void checkVerification()}
              >
                {checkingVerification ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />}
                I entered the code
              </button>
            </div>
          )}

          {currentNumberVerified && (
            <div className="verification-success" role="status">
              <CheckCircle2 size={16} /> Number verified and ready to call
            </div>
          )}

          {manualVerificationRequired && (
            <div className="trial-verification-note" role="note">
              <ShieldCheck size={16} />
              <span>
                Trial account: verify this destination in Twilio first.
                <a
                  href="https://console.twilio.com/us1/develop/phone-numbers/manage/verified"
                  target="_blank"
                  rel="noreferrer"
                >
                  Open Verified Caller IDs
                </a>
              </span>
            </div>
          )}

          <div className="outbound-note">
            <ShieldCheck size={17} />
            <span>The request goes through FastAPI. Twilio and LiveKit credentials remain private.</span>
          </div>

          <div className="outbound-actions">
            <button className="secondary-button" type="button" disabled={busy} onClick={onClose}>Cancel</button>
            {verificationAvailable && !currentNumberVerified && (
              <button
                className="secondary-button verify-button"
                type="button"
                disabled={busy || !phoneNumber.trim()}
                onClick={() => void startVerification()}
              >
                {verifying ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}
                {verifying ? "Calling…" : verificationCode ? "Call again" : "Verify number"}
              </button>
            )}
            <button
              className="save-button"
              type="submit"
              disabled={busy || !phoneNumber.trim() || (verificationRequired && !currentNumberVerified)}
            >
              <PhoneCall size={17} />
              {submitting ? "Starting call…" : "Call now"}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}
