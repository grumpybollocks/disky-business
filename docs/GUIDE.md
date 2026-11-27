# Using Disky Business

Pick a drive in the list on the left; every tab then works on that drive. Under the list are **Refresh**,
**Mount**, **Unmount** and **Safely remove**, and a box with the drive's health. The **Look** button at the top
right changes the colours.

## Test: can this drive be trusted?

1. Pick the drive. If it says "Not mounted", press **Mount**.
2. Read the line under the map: it says how much will be written and what happens to your files. Press
   **Start test**. The test fills the drive's free space with test files, then reads all of it back.
   Each square of the map is a part of the drive. A bar shows how far it is, the speed and the time left.
   **Stop test** says "Stopping..." at once; it ends after the block in hand.
3. Read the verdict.

| Verdict | What it means |
|---|---|
| **Drive is good.** | Everything written came back from the drive, unchanged |
| **Drive is bad** | Some blocks came back wrong or not at all. Do not trust the drive. A stick that claims more space than it has ends here. Before giving up on it, try another port and another cable or card reader, format the drive and test again: a faulty port or reader can look like a faulty drive |
| **Not verified.** | The data matched, but the computer answered from its own memory, so nothing is proven. Unplug the drive, plug it back in and press **Check test files again** |
| **Stopped.** | You stopped it. The test files are still there |

| Map colour | Meaning |
|---|---|
| Not touched | Nothing written there yet |
| Written | Test data written, not yet read back |
| Working now | Where it is working |
| Good | Read back and correct |
| Few bits changed | Came back slightly wrong |
| Bad | Came back wrong |
| Wrong place | Holds test data that belongs somewhere else: the sign of a drive with fake capacity |

**Delete test files** deletes the test files, after asking. It deletes only files that really hold test data.
Your own files are never overwritten or deleted. On the drive your system runs from, the test leaves
5 % of the drive (at least 2 GiB) free. Expect about 40 minutes for a 120 GB SSD and hours for a large hard disk.

## Speed

Press **Start**. One 1 GiB file is written, measured four ways (reading and writing in one go, and many small
reads and writes in random places) and removed again. The four results are shown big, each with a bar; the
graph below shows the speed along the file. Bars grow on a ten-fold scale, so a slow and a fast result both
stay visible. On the drive your system runs from, other programs read and write at the same time, so the
figures can differ from run to run (the app says so); run it twice to compare. On a quiet drive two runs agree
within a few percent.

## SMART: the drive's own health record

The top line says what to do: no warnings, keep a backup, or copy your files off now. The items that matter are
listed first, problems in red; **Show all items** lists the rest.

- **Score, Worst, Fails at**: the drive's own mark for an item. It fails when the score drops to "Fails at".
- **Short self-test** (a few minutes) and **Long self-test** (can take hours): the drive tests itself. Both
  only read. The system asks for the admin password.
- A drive in a USB case often shows nothing. **Read with admin password** reads it another way (with `smartctl`).
  USB sticks and memory cards keep no health record at all: the **Test** tab is the way to check them.
- "Cable errors" usually means the cable or connector, not the drive.
- **Head parkings** and **Motor starts**: hard disks are rated for a set number. When the drive's own score comes
  within reach of its limit, the item turns into a warning ("Near its rated limit").
- Some makers pack several counts into one number or count every operation in their "error rate" items
  (Seagate). Those are shown decoded or explained, not as alarming raw numbers.
- Amounts of data written and read are shown for NVMe drives. For SATA drives the system's figure uses one
  formula for every maker and is often wrong, so it is left out; the **Deep scan** reads the real one.

**Deep scan** reads everything the drive knows about itself (it only reads; admin password). A window opens:

- **Identity**: maker, family, model, firmware, size, interface and its speed, kind (spinning or solid state),
  form factor, TRIM, hours on. The serial number stays hidden until you tick **Show serial number**.
  Production date: SATA and NVMe drives do not report one; only SAS drives do, so it says "not reported".
- **Errors the drive logged**: when, and what went wrong. Old errors that do not grow are less worrying than
  new ones: scan again later and compare.
- **Self-test history**: passed, stopped or failed.
- **Lifetime statistics**: data written and read, times switched on, spots replaced, hottest ever, wear used.
- **Link errors**: damaged data on the cable points to the cable or port, not the drive.
- **Temperature record**: the lowest and highest of recent days, and now.

**Save report...** writes it all to a text file, the serial number left out unless ticked.

## Defrag

**Scan** shows how fragmented the files are and lists the worst. On btrfs, ext4 and XFS, **Defragment all**
then works through every fragmented file with the system's own tool; after each file its content is checked to
be unchanged. **Stop** ends it after the file in hand. exFAT is scanned only (this needs admin rights). Windows
(NTFS) drives are not scanned or defragmented: Linux has no tool for that which can be trusted, and Windows
does it by itself.

On btrfs:

- **Compressed files** (`compress=` mount option) are stored in 128 KiB pieces that lie one after another. They
  are counted as one piece, not one per 128 KiB, so a compressed file is not called fragmented for nothing.
- **Files shared with a snapshot or a copy** (Timeshift, Snapper, `cp --reflink`) are listed but never
  defragmented: defragmenting unshares them, and their data would be stored a second time. The scan says how many
  there are, and "Defragment all" offers only the others.
- If a file's content changes while it is being defragmented, the run stops at once and nothing more is
  touched. The message says whether another program was most likely writing to it (close it and run again) or
  the system's tool changed it (stop using it on that drive).

## Care: check, repair, trim

Pick a partition. It must be unmounted first (**Unmount**, on the left).

- **Check** only reads and says whether the file system is sound.
- **Repair** lets the system's own tool fix it. This changes the drive. Copy off what matters first, if you
  still can; a repair can remove damaged files, and it cannot be stopped once started.
- Windows (NTFS) partitions are not repaired here; use Windows' own disk check.
- **Trim this drive** tells an SSD which space is free, for every mounted file system on it. Linux does this
  weekly by itself, so it is rarely needed, and most USB cases do not pass it on. A file system mounted with
  the `discard` option (btrfs `discard=async`, for example) trims as files are deleted; the tab says so.

## When a drive cannot be used

| Message | What to do |
|---|---|
| Not mounted | Press **Mount** |
| Mounted read-only | The message says why. A Mac-formatted drive is read-only on Linux while its journaling is on |
| NTFS (Windows) drive: not touched automatically | Linux may change a damaged NTFS drive just by reading it. Use **Other folder...** only if Windows' disk check passed |

## The password

Most things need no password. TRIM, the exFAT scan, SMART through a USB case, the deep scan and self-tests need admin rights.
The app asks you first, and the password is asked **once per session**: after that an "Admin session open: End"
button shows at the bottom, and no further password is asked until you press it or close the app. That session
runs only Disky's own jobs. Mounting or checking a drive that is built into the computer may bring up the
system's own password window directly.

## Looks

The **Look** button switches between Modern light, Modern dark, Windows 98 style, Dark and Deep violet. A first
start takes Modern, light or dark as your system is set. The window remembers its look and its size.
