import type { ReactNode } from 'react'
import { BedDouble, Watch } from 'lucide-react'
import { RelativeTime } from '../../components/RelativeTime'
import type { BedroomDevice, WearableDevice } from '../../types/resident'
import { DeviceBattery, OnlineStatus } from '../dashboard/DeviceStatus'
import { DetailSection } from './DetailSection'

interface DevicesSectionProps {
  wearable: WearableDevice | null
  bedroom: BedroomDevice | null
  now: Date
}

/** The resident's wearable and bedroom sensor (pattern 10), from their latest `status`. */
export function DevicesSection({ wearable, bedroom, now }: DevicesSectionProps) {
  return (
    <DetailSection title="Devices">
      {!wearable && !bedroom ? (
        <p className="text-base text-slate-600">No devices are assigned to this resident.</p>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          <li>
            {wearable ? (
              <WearableItem device={wearable} now={now} />
            ) : (
              <EmptyDevice icon={<Watch className="size-5" aria-hidden="true" />} text="No wearable assigned." />
            )}
          </li>
          <li>
            {bedroom ? (
              <BedroomItem device={bedroom} now={now} />
            ) : (
              <EmptyDevice
                icon={<BedDouble className="size-5" aria-hidden="true" />}
                text="No bedroom sensor assigned."
              />
            )}
          </li>
        </ul>
      )}
    </DetailSection>
  )
}

function WearableItem({ device, now }: { device: WearableDevice; now: Date }) {
  const status = device.latest.status[0]
  return (
    <DeviceBox icon={<Watch className="size-5" aria-hidden="true" />} name="Wearable" deviceId={device.device_id}>
      <Fact label="Connection" value={<OnlineStatus online={device.online} />} />
      {status ? (
        <>
          <Fact label="Battery" value={<DeviceBattery battery_pct={status.battery_pct} charging={status.charging} mainsPowered={false} />} />
          <Fact
            label="State"
            value={`${status.worn ? 'Worn' : 'Not worn'}${status.charging ? ' · Charging' : ''}`}
          />
          <Fact label="Last seen" value={<RelativeTime iso={status.ts} now={now} />} />
          <Fact label="Firmware" value={status.fw_version} />
        </>
      ) : (
        <Fact label="Last seen" value="No status message yet" />
      )}
    </DeviceBox>
  )
}

function BedroomItem({ device, now }: { device: BedroomDevice; now: Date }) {
  const status = device.latest.status[0]
  return (
    <DeviceBox
      icon={<BedDouble className="size-5" aria-hidden="true" />}
      name="Bedroom sensor"
      deviceId={device.device_id}
    >
      <Fact label="Connection" value={<OnlineStatus online={device.online} />} />
      {status ? (
        <>
          <Fact label="Battery" value={<DeviceBattery battery_pct={status.battery_pct} charging={false} mainsPowered />} />
          <Fact label="Last seen" value={<RelativeTime iso={status.ts} now={now} />} />
          <Fact label="Firmware" value={status.fw_version} />
        </>
      ) : (
        <Fact label="Last seen" value="No status message yet" />
      )}
    </DeviceBox>
  )
}

function DeviceBox({
  icon,
  name,
  deviceId,
  children,
}: {
  icon: ReactNode
  name: string
  deviceId: string
  children: ReactNode
}) {
  return (
    <div className="flex h-full flex-col gap-3 rounded-xl border border-slate-200 p-4">
      <h3 className="flex flex-wrap items-center gap-2 text-base font-semibold text-slate-900">
        <span className="text-slate-600">{icon}</span>
        {/* One inline span, so the space before "·" isn't dropped between flex items. */}
        <span>
          {name} <span className="font-normal text-slate-600">· {deviceId}</span>
        </span>
      </h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-base">{children}</dl>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <>
      <dt className="text-slate-600">{label}</dt>
      <dd className="font-medium text-slate-900">{value}</dd>
    </>
  )
}

function EmptyDevice({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <p className="flex h-full items-center gap-2 rounded-xl border border-dashed border-slate-300 p-4 text-base text-slate-600">
      {icon}
      {text}
    </p>
  )
}
