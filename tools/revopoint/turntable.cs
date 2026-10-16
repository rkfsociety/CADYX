using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;

internal static class Program
{
    private static readonly Guid ServiceUuid = Guid.Parse("0000FFE0-0000-1000-8000-00805F9B34FB");
    private static readonly Guid CharacteristicUuid = Guid.Parse("0000FFE1-0000-1000-8000-00805F9B34FB");
    private const string DeviceName = "REVO_DUAL_AXIS_TABLE";
    private const double MaxDegreesPerCommand = 30.0;
    private const double MinRotationPeriodSeconds = 18.0;
    private const double MaxRotationPeriodSeconds = 90.0;

    private sealed class Connection : IDisposable
    {
        public BluetoothLEDevice Device;
        public GattDeviceService Service;
        public GattCharacteristic Characteristic;
        public ConcurrentQueue<string> Responses = new ConcurrentQueue<string>();
        public TypedEventHandler<GattCharacteristic, GattValueChangedEventArgs> Handler;
        public bool NotificationsEnabled;

        public void Dispose()
        {
            if (Characteristic != null && Handler != null)
            {
                Characteristic.ValueChanged -= Handler;
            }
            if (NotificationsEnabled && Characteristic != null)
            {
                Characteristic.WriteClientCharacteristicConfigurationDescriptorAsync(
                    GattClientCharacteristicConfigurationDescriptorValue.None).AsTask().GetAwaiter().GetResult();
            }
            if (Service != null) Service.Dispose();
            if (Device != null) Device.Dispose();
        }
    }

    private static int Main(string[] args)
    {
        try
        {
            if (args.Length == 0 || args[0] == "--help" || args[0] == "-h")
            {
                Usage();
                return args.Length == 0 ? 2 : 0;
            }

            string operation = args[0].ToLowerInvariant();
            double degrees = 0;
            double rotationPeriod = 0;
            int monitorSeconds = 0;
            int sweepSteps = 0;
            string monitorPath = null;
            int rotateAfterSeconds = -1;
            if (operation == "rotate")
            {
                if (args.Length != 2 || !double.TryParse(args[1], NumberStyles.Float,
                        CultureInfo.InvariantCulture, out degrees) || !IsFinite(degrees) ||
                    Math.Abs(degrees) < 0.1 || Math.Abs(degrees) > MaxDegreesPerCommand)
                {
                    throw new ArgumentException("rotate requires a number from -30 to 30 degrees, excluding zero.");
                }
            }
            else if (operation == "speed")
            {
                if (args.Length != 2 || !double.TryParse(args[1], NumberStyles.Float,
                        CultureInfo.InvariantCulture, out rotationPeriod) || !IsFinite(rotationPeriod) ||
                    rotationPeriod < MinRotationPeriodSeconds || rotationPeriod > MaxRotationPeriodSeconds)
                {
                    throw new ArgumentException("speed requires seconds per rotation from 18 to 90.");
                }
            }
            else if (operation == "tilt")
            {
                if (args.Length != 2 || !double.TryParse(args[1], NumberStyles.Float,
                        CultureInfo.InvariantCulture, out degrees) || !IsFinite(degrees) || Math.Abs(degrees) > 30.0)
                    throw new ArgumentException("tilt requires a target angle from -30 to 30 degrees.");
            }
            else if (operation == "monitor")
            {
                if ((args.Length != 3 && args.Length != 5) || !Int32.TryParse(args[1], out monitorSeconds) || monitorSeconds < 1 || monitorSeconds > 300)
                    throw new ArgumentException("monitor requires duration (1–300 s), CSV path, and optional rotate-delay plus degrees.");
                monitorPath = args[2];
                if (args.Length == 5 && (!Int32.TryParse(args[3], out rotateAfterSeconds) || rotateAfterSeconds < 0 || rotateAfterSeconds >= monitorSeconds ||
                    !double.TryParse(args[4], NumberStyles.Float, CultureInfo.InvariantCulture, out degrees) || !IsFinite(degrees) ||
                    Math.Abs(degrees) < 0.1 || Math.Abs(degrees) > MaxDegreesPerCommand))
                    throw new ArgumentException("monitor rotation requires delay < duration and degrees from -30 to 30.");
            }
            else if (operation == "sweep")
            {
                if (args.Length != 4 || !Int32.TryParse(args[1], out sweepSteps) || sweepSteps < 1 || sweepSteps > 360 ||
                    !double.TryParse(args[2], NumberStyles.Float, CultureInfo.InvariantCulture, out degrees) ||
                    !IsFinite(degrees) || Math.Abs(degrees) < 0.1 || Math.Abs(degrees) > MaxDegreesPerCommand)
                    throw new ArgumentException("sweep requires 1–360 steps, each from -30 to 30 degrees, and a CSV output path.");
                if (Math.Abs(degrees * sweepSteps) > 360.0)
                    throw new ArgumentException("sweep cannot command more than 360 degrees of total rotation.");
                monitorPath = args[3];
            }
            else if ((operation == "query" || operation == "zero" || operation == "stop") && args.Length != 1)
            {
                throw new ArgumentException("query, zero and stop do not accept extra arguments.");
            }
            else if (operation != "query" && operation != "zero" && operation != "stop" && operation != "speed" && operation != "tilt" && operation != "monitor" && operation != "sweep")
            {
                throw new ArgumentException("Operation must be query, zero, stop, speed, tilt, monitor, sweep, or rotate.");
            }

            return RunAsync(operation, degrees, rotationPeriod, monitorSeconds, monitorPath, rotateAfterSeconds, sweepSteps).GetAwaiter().GetResult();
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("Turntable command failed: " + error.Message);
            return 1;
        }
    }

    private static void Usage()
    {
        Console.WriteLine("TurntableController.exe query");
        Console.WriteLine("TurntableController.exe zero");
        Console.WriteLine("TurntableController.exe speed <seconds-per-rotation>   (18–90 seconds)");
        Console.WriteLine("TurntableController.exe tilt <degrees>   (-30–30 degrees)");
        Console.WriteLine("TurntableController.exe rotate <degrees>   (one bounded relative move, max 30 degrees)");
        Console.WriteLine("TurntableController.exe stop");
        Console.WriteLine("TurntableController.exe monitor <seconds> <output.csv>");
        Console.WriteLine("TurntableController.exe monitor <seconds> <output.csv> <rotate-delay-seconds> <degrees>");
        Console.WriteLine("TurntableController.exe sweep <steps> <degrees-per-step> <output.csv>   (one BLE connection; each step is verified before the next)");
        Console.WriteLine("The app does not pair the turntable. It discovers the BLE device by its advertised FFE1 service,\n" +
                          "then confirms the GATT device name before sending any command.");
    }

    private static async Task<int> RunAsync(string operation, double degrees, double rotationPeriod,
                                            int monitorSeconds, string monitorPath, int rotateAfterSeconds,
                                            int sweepSteps)
    {
        using (Connection connection = await ConnectAsync())
        {
            await EnableNotificationsAsync(connection);
            if (operation == "query")
            {
                foreach (string command in new[] { "+QT,CHANGEANGLE;", "+QR,TILTVALUE;", "+QT,TURNSPEED;", "+QR,TILTSPEED;" })
                {
                    Console.WriteLine(command + " => " + await SendAndReadAsync(connection, command));
                }
                return 0;
            }

            if (operation == "monitor")
            {
                string fullPath = System.IO.Path.GetFullPath(monitorPath);
                using (var writer = new System.IO.StreamWriter(fullPath, false, new UTF8Encoding(false)))
                {
                    writer.WriteLine("utc,angle_degrees,tilt_degrees,rotation_response,tilt_response");
                    var monitorDeadline = DateTime.UtcNow.AddSeconds(monitorSeconds);
                    var monitorTimer = Stopwatch.StartNew();
                    int sample = 0;
                    bool moveSent = false;
                    while (DateTime.UtcNow < monitorDeadline)
                    {
                        if (!moveSent && rotateAfterSeconds >= 0 && monitorTimer.Elapsed.TotalSeconds >= rotateAfterSeconds)
                        {
                            string monitorMove = "+CT,TURNANGLE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
                            Console.WriteLine("issuing " + monitorMove);
                            string reply = await SendAndReadAsync(connection, monitorMove);
                            writer.WriteLine("# " + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + " command " + monitorMove + " => " + reply);
                            writer.Flush();
                            moveSent = true;
                        }
                        string response = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                        string tiltResponse = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                        double angle, tilt;
                        DateTime observedAt = DateTime.UtcNow;
                        if (TryAngle(response, out angle) && TryAngle(tiltResponse, out tilt))
                        {
                            writer.WriteLine(observedAt.ToString("o", CultureInfo.InvariantCulture) + "," +
                                angle.ToString("F2", CultureInfo.InvariantCulture) + "," +
                                tilt.ToString("F2", CultureInfo.InvariantCulture) + ",\"" + response.Replace("\"", "\"\"") + "\",\"" +
                                tiltResponse.Replace("\"", "\"\"") + "\"");
                            writer.Flush();
                            if (sample++ % 5 == 0) Console.WriteLine(observedAt.ToString("o") + " angle=" + angle.ToString("F1", CultureInfo.InvariantCulture));
                        }
                        await Task.Delay(50);
                    }
                }
                return 0;
            }

            if (operation == "sweep")
            {
                string fullPath = System.IO.Path.GetFullPath(monitorPath);
                if (System.IO.File.Exists(fullPath))
                    throw new InvalidOperationException("Sweep output already exists; refusing to overwrite: " + fullPath);
                string directory = System.IO.Path.GetDirectoryName(fullPath);
                if (!String.IsNullOrEmpty(directory)) System.IO.Directory.CreateDirectory(directory);
                using (var writer = new System.IO.StreamWriter(fullPath, false, new UTF8Encoding(false)))
                {
                    writer.WriteLine("utc,step_index,event,target_delta_degrees,angle_degrees,tilt_degrees,command,rotation_response,tilt_response");
                    string initialRotation = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                    string initialTilt = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                    double currentAngle, currentTilt;
                    if (!TryAngle(initialRotation, out currentAngle) || !TryAngle(initialTilt, out currentTilt))
                        throw new InvalidOperationException("Could not read both table axes; no sweep command was sent.");
                    if (Math.Abs(currentTilt) > 0.5)
                        throw new InvalidOperationException("Turntable tilt must be within 0.5 degrees of zero before a rotation-only sweep.");
                    WriteSweepRow(writer, 0, "baseline", 0, currentAngle, currentTilt, "", initialRotation, initialTilt);
                    Console.WriteLine("sweep baseline: angle=" + currentAngle.ToString("F1", CultureInfo.InvariantCulture) +
                                      " deg, tilt=" + currentTilt.ToString("F1", CultureInfo.InvariantCulture) + " deg");

                    for (int step = 1; step <= sweepSteps; ++step)
                    {
                        double sweepBefore = currentAngle;
                        string command = "+CT,TURNANGLE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
                        string commandReply = await SendAndReadAsync(connection, command);
                        WriteSweepRow(writer, step, "command_sent", degrees, sweepBefore, currentTilt, command, commandReply, "");
                        Console.WriteLine("sweep step " + step + "/" + sweepSteps + ": " + command + " => " + commandReply);

                        bool reached = false;
                        DateTime sweepDeadline = DateTime.UtcNow.AddSeconds(10.0 + Math.Abs(degrees) * 90.0 / 360.0);
                        while (DateTime.UtcNow < sweepDeadline)
                        {
                            string rotationResponse = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                            string tiltResponse = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                            double angle, tilt;
                            if (TryAngle(rotationResponse, out angle) && TryAngle(tiltResponse, out tilt))
                            {
                                currentAngle = angle;
                                currentTilt = tilt;
                                double sweepMeasured = SignedAngleDelta(sweepBefore, currentAngle);
                                bool targetReached = Math.Abs(sweepMeasured - degrees) <= 1.0;
                                WriteSweepRow(writer, step, targetReached ? "target_reached" : "telemetry",
                                    degrees, currentAngle, currentTilt, "", rotationResponse, tiltResponse);
                                if (Math.Abs(currentTilt) > 0.5)
                                {
                                    await SendAndReadAsync(connection, "+CT,STOP;");
                                    throw new InvalidOperationException("Turntable tilt changed during rotation; rotation was stopped.");
                                }
                                if (targetReached)
                                {
                                    reached = true;
                                    Console.WriteLine("step " + step + " reached: angle=" + currentAngle.ToString("F1", CultureInfo.InvariantCulture) +
                                                      " deg, measured delta=" + sweepMeasured.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                                    break;
                                }
                            }
                            else
                            {
                                WriteSweepRow(writer, step, "unreadable_telemetry", degrees, Double.NaN, Double.NaN,
                                    "", rotationResponse, tiltResponse);
                            }
                        }
                        if (!reached)
                        {
                            await SendAndReadAsync(connection, "+CT,STOP;");
                            throw new InvalidOperationException("Sweep step " + step + " did not reach its target; no further move was issued.");
                        }
                    }
                    Console.WriteLine("sweep completed: " + sweepSteps + " steps, commanded total=" +
                                      (sweepSteps * degrees).ToString("F1", CultureInfo.InvariantCulture) + " deg");
                }
                return 0;
            }

            if (operation == "stop")
            {
                Console.WriteLine("rotation stop: " + await SendAndReadAsync(connection, "+CT,STOP;"));
                Console.WriteLine("tilt stop: " + await SendAndReadAsync(connection, "+CR,STOP;"));
                return 0;
            }

            if (operation == "speed")
            {
                string command = "+CT,TURNSPEED=" + rotationPeriod.ToString("F1", CultureInfo.InvariantCulture) + ";";
                Console.WriteLine(command + " => " + await SendAndReadAsync(connection, command));
                Console.WriteLine("Requested rotation period: " + rotationPeriod.ToString("F1", CultureInfo.InvariantCulture) + " seconds per revolution.");
                return 0;
            }

            if (operation == "tilt")
            {
                string tiltBeforeText = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                double tiltBefore;
                if (!TryAngle(tiltBeforeText, out tiltBefore))
                    throw new InvalidOperationException("Could not read current tilt angle; no tilt command was sent.");
                Console.WriteLine("tilt before: " + tiltBefore.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                string command = "+CR,TILTVALUE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
                Console.WriteLine(command + " => " + await SendAndReadAsync(connection, command));
                DateTime tiltDeadline = DateTime.UtcNow.AddSeconds(30);
                double tiltAfter = Double.NaN;
                while (DateTime.UtcNow < tiltDeadline)
                {
                    await Task.Delay(300);
                    string response = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                    if (TryAngle(response, out tiltAfter) && Math.Abs(tiltAfter - degrees) <= 1.0)
                    {
                        Console.WriteLine("tilt after: " + tiltAfter.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                        return 0;
                    }
                }
                Console.Error.WriteLine("Tilt did not reach the requested angle within 30 s.");
                return 1;
            }

            if (operation == "zero")
            {
                string zeroBaselineText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                double zeroBaseline;
                if (!TryAngle(zeroBaselineText, out zeroBaseline))
                {
                    throw new InvalidOperationException("Could not read the current angle; zero command was not sent.");
                }
                string tiltBaselineText = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                double tiltBaseline;
                if (!TryAngle(tiltBaselineText, out tiltBaseline))
                {
                    throw new InvalidOperationException("Could not read the current tilt; zero commands were not sent.");
                }
                Console.WriteLine("angle before zero: " + zeroBaseline.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                Console.WriteLine("tilt before zero: " + tiltBaseline.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                string reply = await SendAndReadAsync(connection, "+CT,TOZERO;");
                Console.WriteLine("+CT,TOZERO; => " + reply);
                string tiltReply = await SendAndReadAsync(connection, "+CR,TOZERO;");
                Console.WriteLine("+CR,TOZERO; => " + tiltReply);
                for (int attempt = 0; attempt < 30; ++attempt)
                {
                    await Task.Delay(1000);
                    string currentText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                    string currentTiltText = await SendAndReadAsync(connection, "+QR,TILTVALUE;");
                    double current, currentTilt;
                    if (TryAngle(currentText, out current) && TryAngle(currentTiltText, out currentTilt))
                    {
                        double normalized = SignedAngleDelta(0, current);
                        if (Math.Abs(normalized) <= 0.5 && Math.Abs(currentTilt) <= 0.5)
                        {
                            Console.WriteLine("angle after zero: " + normalized.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                            Console.WriteLine("tilt after zero: " + currentTilt.ToString("F1", CultureInfo.InvariantCulture) + " deg");
                            return 0;
                        }
                    }
                }
                Console.Error.WriteLine("Rotation or tilt did not report zero within 30 checks; no automatic retry was sent.");
                return 1;
            }

            string beforeText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
            double before;
            if (!TryAngle(beforeText, out before))
            {
                throw new InvalidOperationException("Could not read the current angle; no movement was sent.");
            }
            Console.WriteLine("angle before: " + before.ToString("F1", CultureInfo.InvariantCulture) + " deg");

            string move = "+CT,TURNANGLE=" + degrees.ToString("F1", CultureInfo.InvariantCulture) + ";";
            var timer = Stopwatch.StartNew();
            string moveReply = await SendAndReadAsync(connection, move);
            Console.WriteLine(move + " => " + moveReply);
            double after = Double.NaN;
            double measured = Double.NaN;
            bool reachedTarget = false;
            DateTime deadline = DateTime.UtcNow.AddSeconds(10.0 + Math.Abs(degrees) * 90.0 / 360.0);
            while (DateTime.UtcNow < deadline)
            {
                await Task.Delay(200);
                string afterText = await SendAndReadAsync(connection, "+QT,CHANGEANGLE;");
                if (!TryAngle(afterText, out after)) continue;
                measured = SignedAngleDelta(before, after);
                if (Math.Abs(measured - degrees) <= 1.0)
                {
                    reachedTarget = true;
                    timer.Stop();
                    break;
                }
            }
            if (Double.IsNaN(after))
            {
                Console.WriteLine("angle after: unavailable; no further motor command was sent");
                return 1;
            }
            Console.WriteLine("angle after: " + after.ToString("F1", CultureInfo.InvariantCulture) + " deg");
            Console.WriteLine("measured change: " + measured.ToString("F1", CultureInfo.InvariantCulture) + " deg");
            if (reachedTarget)
            {
                Console.WriteLine("movement completion time: " + timer.Elapsed.TotalSeconds.ToString("F2", CultureInfo.InvariantCulture) + " s");
            }
            if (!reachedTarget)
            {
                Console.Error.WriteLine("Rotation did not reach the target before timeout; do not retry automatically.");
                return 1;
            }
            return 0;
        }
    }

    private static async Task<Connection> ConnectAsync()
    {
        Console.WriteLine("Searching nearby BLE devices for the Revopoint turntable...");
        var addresses = new ConcurrentDictionary<ulong, byte>();
        var watcher = new BluetoothLEAdvertisementWatcher { ScanningMode = BluetoothLEScanningMode.Active };
        TypedEventHandler<BluetoothLEAdvertisementWatcher, BluetoothLEAdvertisementReceivedEventArgs> handler = (sender, eventArgs) =>
        {
            if (eventArgs.Advertisement.ServiceUuids.Contains(CharacteristicUuid))
            {
                addresses[eventArgs.BluetoothAddress] = 0;
            }
        };
        watcher.Received += handler;
        try
        {
            watcher.Start();
            await Task.Delay(TimeSpan.FromSeconds(8));
        }
        finally
        {
            try { watcher.Stop(); } catch { }
            watcher.Received -= handler;
        }

        foreach (ulong address in addresses.Keys)
        {
            BluetoothLEDevice device = null;
            try
            {
                device = await BluetoothLEDevice.FromBluetoothAddressAsync(address);
                if (device == null || !String.Equals(device.Name, DeviceName, StringComparison.Ordinal))
                {
                    if (device != null) device.Dispose();
                    continue;
                }

                var serviceResult = await device.GetGattServicesForUuidAsync(ServiceUuid, BluetoothCacheMode.Uncached);
                if (serviceResult.Status != GattCommunicationStatus.Success || serviceResult.Services.Count != 1)
                {
                    foreach (var unmatchedService in serviceResult.Services) unmatchedService.Dispose();
                    device.Dispose();
                    continue;
                }

                GattDeviceService service = serviceResult.Services[0];
                var characteristicResult = await service.GetCharacteristicsForUuidAsync(CharacteristicUuid, BluetoothCacheMode.Uncached);
                if (characteristicResult.Status != GattCommunicationStatus.Success || characteristicResult.Characteristics.Count != 1)
                {
                    service.Dispose();
                    device.Dispose();
                    continue;
                }

                GattCharacteristic characteristic = characteristicResult.Characteristics[0];
                var properties = characteristic.CharacteristicProperties;
                if ((properties & GattCharacteristicProperties.Notify) == 0 ||
                    ((properties & GattCharacteristicProperties.WriteWithoutResponse) == 0 &&
                     (properties & GattCharacteristicProperties.Write) == 0))
                {
                    service.Dispose();
                    device.Dispose();
                    continue;
                }
                Console.WriteLine("Connected to " + DeviceName + " over BLE (no pairing performed).");
                return new Connection { Device = device, Service = service, Characteristic = characteristic };
            }
            catch
            {
                if (device != null) device.Dispose();
            }
        }
        throw new InvalidOperationException("No nearby BLE device matched REVO_DUAL_AXIS_TABLE with the expected GATT service.");
    }

    private static async Task EnableNotificationsAsync(Connection connection)
    {
        connection.Handler = (sender, eventArgs) => connection.Responses.Enqueue(ReadAscii(eventArgs.CharacteristicValue));
        connection.Characteristic.ValueChanged += connection.Handler;
        var status = await connection.Characteristic.WriteClientCharacteristicConfigurationDescriptorAsync(
            GattClientCharacteristicConfigurationDescriptorValue.Notify);
        if (status != GattCommunicationStatus.Success)
        {
            throw new InvalidOperationException("Could not enable turntable status notifications: " + status);
        }
        connection.NotificationsEnabled = true;
    }

    private static async Task<string> SendAndReadAsync(Connection connection, string command)
    {
        string ignored;
        while (connection.Responses.TryDequeue(out ignored)) { }

        var writer = new DataWriter();
        writer.WriteBytes(Encoding.ASCII.GetBytes(command + "\r\n"));
        GattWriteOption option =
            (connection.Characteristic.CharacteristicProperties & GattCharacteristicProperties.WriteWithoutResponse) != 0
                ? GattWriteOption.WriteWithoutResponse
                : GattWriteOption.WriteWithResponse;
        var writeStatus = await connection.Characteristic.WriteValueAsync(writer.DetachBuffer(), option);
        if (writeStatus != GattCommunicationStatus.Success)
        {
            throw new InvalidOperationException("BLE write failed for " + command + ": " + writeStatus);
        }
        await Task.Delay(200);

        string response;
        string last = null;
        while (connection.Responses.TryDequeue(out response)) last = response;
        if (last != null) return last;

        var read = await connection.Characteristic.ReadValueAsync(BluetoothCacheMode.Uncached);
        if (read.Status != GattCommunicationStatus.Success)
        {
            throw new InvalidOperationException("No BLE notification and readback failed for " + command + ".");
        }
        return ReadAscii(read.Value);
    }

    private static string ReadAscii(IBuffer buffer)
    {
        if (buffer == null || buffer.Length == 0) return "<empty>";
        var reader = DataReader.FromBuffer(buffer);
        var bytes = new byte[reader.UnconsumedBufferLength];
        reader.ReadBytes(bytes);
        return Encoding.ASCII.GetString(bytes).Replace("\r", "").Replace("\n", "");
    }

    private static void WriteSweepRow(System.IO.StreamWriter writer, int step, string eventName,
                                      double targetDelta, double angle, double tilt, string command,
                                      string rotationResponse, string tiltResponse)
    {
        writer.WriteLine(String.Join(",", new[] {
            DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
            step.ToString(CultureInfo.InvariantCulture),
            Csv(eventName),
            targetDelta.ToString("F2", CultureInfo.InvariantCulture),
            IsFinite(angle) ? angle.ToString("F2", CultureInfo.InvariantCulture) : "",
            IsFinite(tilt) ? tilt.ToString("F2", CultureInfo.InvariantCulture) : "",
            Csv(command), Csv(rotationResponse), Csv(tiltResponse),
        }));
        writer.Flush();
    }

    private static string Csv(string value)
    {
        return "\"" + (value ?? "").Replace("\"", "\"\"") + "\"";
    }

    private static bool TryAngle(string response, out double angle)
    {
        Match match = Regex.Match(response ?? String.Empty, @"\+DATA=\s*(-?\d+(?:\.\d+)?)");
        return double.TryParse(match.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out angle);
    }

    private static double SignedAngleDelta(double before, double after)
    {
        double delta = after - before;
        if (delta > 180.0) delta -= 360.0;
        if (delta < -180.0) delta += 360.0;
        return delta;
    }

    private static bool IsFinite(double value)
    {
        return !Double.IsNaN(value) && !Double.IsInfinity(value);
    }
}
