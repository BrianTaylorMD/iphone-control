# WebDriverAgent shows "Not Verified" and Verify App does nothing

Apps signed by a free or paid development team must be verified online once, through Apple's `ppq.apple.com`, before
iOS lets them run. When **Verify App** does nothing, find out *why* from the phone's own log before changing anything.

## 1. Look at the phone's log while tapping Verify App

```bash
pymobiledevice3 syslog live | grep -E "online-auth-agent|mobileactivationd|unable to attest|BAA|ppq|Authorized|REJECT"
```

| You see | Meaning | Fix |
|---|---|---|
| `unable to attest key` → `Failed to create BAA info` → `Could not perform authorization attempt` | The phone can't produce its device-identity attestation, so the request never leaves the phone. Network settings are irrelevant. | **Restart the phone**, unlock it, retry. This fixed it on our test phone; Apple then answered `authorized: true`. |
| `Performing auth request` / `ppq.apple.com` then timeouts or HTTP errors | The phone can't reach Apple, or Apple's service is degraded. | Try another network (cellular), turn off VPN/DNS filters/content blockers/iCloud Private Relay, and set Date & Time to automatic. Apple's service has had outages before. |
| `Rejected profile`, `REJECT_PROFILE`, `banned` | Apple refused the team or profile. | Re-sign with another Apple ID or a paid team. |
| `Authorized:` | Verification worked. | Launch WDA again (`wda start`). |

## 2. Safe fixes, cheapest first

1. Restart the phone, unlock it, tap Verify App again.
2. Settings → General → Date & Time → Set Automatically (turn it off and back on).
3. Temporarily turn off VPN apps, DNS/filter profiles, iCloud Private Relay and Network Link Conditioner (Settings →
   Developer).
4. Accept any pending agreement at developer.apple.com/account.
5. Install pending iOS updates, then pair with the Mac again.

## 3. Last resorts (each has a cost)

- **Delete and reinstall WebDriverAgent.** This removes the developer's trust, so you'll trust it again.
- **Reset Network Settings.** This erases Wi-Fi passwords and VPN configs.
- **Erase the phone.** This deletes everything. Only if nothing else works.
