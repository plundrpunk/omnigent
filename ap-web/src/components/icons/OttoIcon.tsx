import { forwardRef, type SVGProps } from "react";

// AUTOMATON mascot: a greaser wolf — pompadour, leather jacket, circle-A
// patch — with a pup sidekick (replaces the Otto starfish; keeps Otto's
// animation contract so OttoEyes/index.css need no changes):
// - Pass className="otto-working" for the bob + blink (see index.css). Each eye
//   (the wolf's two and the buddy pup's two) is a `g.otto-eye` group of
//   sclera + pupil + glint so the blink collapses each eye in place.
// - The wolf's two pupils (black disc + glint) are additionally wrapped in
//   `g.otto-pupil` groups that OttoEyes slides toward the cursor; the ref is
//   forwarded to the root svg so OttoEyes can query them. The buddy's eyes
//   have no pupil group and stay still.
// - Eye centers (413.8, 520.6) / (619.1, 520.6), sclera r=71.3, pupil r=55.9
//   match the geometry constants hardcoded in OttoEyes.tsx.
export const OttoIcon = forwardRef<SVGSVGElement, SVGProps<SVGSVGElement>>(
  function OttoIcon(props, ref) {
    return (
      <svg ref={ref} viewBox="0 0 1024 1024" fill="none" aria-hidden="true" {...props}>
        {/* Ears — tall angular triangles. */}
        <path d="M336 330 L242 100 L462 282 Z" fill="#FFD700" />
        <path d="M696 330 L790 100 L570 282 Z" fill="#FFD700" />
        {/* Inner ears. */}
        <path d="M348 300 L286 148 L430 270 Z" fill="#2B4ECC" />
        <path d="M684 300 L746 148 L602 270 Z" fill="#2B4ECC" />
        {/* Head — angular silhouette with spiked cheek fur and a V chin. */}
        <path
          d="M336 316 C400 284 632 284 696 316 L724 470 L788 540 L706 588 L768 668 L664 706 L588 810 L516 848 L444 810 L368 706 L264 668 L326 588 L244 540 L308 470 Z"
          fill="#FFD700"
        />
        {/* Pompadour — solid mound with a front quiff and slick highlights. */}
        <path d="M330 320 Q330 130 516 118 Q696 130 696 320 Z" fill="#4A7CF0" />
        <path d="M394 158 C426 78 546 60 610 112 C544 92 462 106 424 172 Z" fill="#4A7CF0" />
        <path d="M560 252 C620 202 664 224 676 288 C644 254 602 252 560 252 Z" fill="#8FC8F5" />
        <path d="M414 148 C450 96 530 84 584 110 C528 100 462 112 430 160 Z" fill="#8FC8F5" />
        <path
          d="M380 300 C390 210 450 160 520 150 L524 166 C462 176 408 222 396 302 Z"
          fill="#2B4ECC"
        />
        {/* Brows — angled down for the cool scowl. */}
        <path d="M320 424 L474 462 L478 492 L326 452 Z" fill="#2B4ECC" />
        <path d="M712 424 L558 462 L554 492 L706 452 Z" fill="#2B4ECC" />
        {/* Scar — slashes over the right brow. */}
        <path d="M610 350 L622 344 L648 424 L636 430 Z" fill="#2B4ECC" />
        <path d="M646 342 L658 336 L684 416 L672 422 Z" fill="#2B4ECC" />
        {/* Muzzle shading. */}
        <path d="M516 588 L610 690 L588 772 L516 806 L444 772 L422 690 Z" fill="#C79200" />
        {/* Nose. */}
        <path d="M472 646 L560 646 L516 706 Z" fill="#0D0D0D" />
        {/* Smirk — angled mouth line with bared fangs. */}
        <path d="M448 752 L586 736 L588 750 L450 766 Z" fill="#0D0D0D" />
        <path d="M468 752 L484 796 L500 748 Z" fill="#FEFEFE" />
        <path d="M540 744 L554 788 L568 741 Z" fill="#FEFEFE" />
        {/* Hoop earrings on the left ear. */}
        <path
          d="M268 242 a24 24 0 1 0 48 0 a24 24 0 1 0 -48 0 M282 242 a10 10 0 1 1 20 0 a10 10 0 1 1 -20 0"
          fill="#4A7CF0"
          fillRule="evenodd"
        />
        <path
          d="M299 300 a19 19 0 1 0 38 0 a19 19 0 1 0 -38 0 M310 300 a8 8 0 1 1 16 0 a8 8 0 1 1 -16 0"
          fill="#4A7CF0"
          fillRule="evenodd"
        />
        {/* Neck. */}
        <path d="M470 820 L562 820 L552 910 L480 910 Z" fill="#C79200" />
        {/* Leather jacket — shoulders with popped collar. */}
        <path
          d="M150 1024 L150 940 C220 850 330 812 400 800 L516 872 L632 800 C702 812 812 850 874 940 L874 1024 Z"
          fill="#26262b"
        />
        <path d="M400 800 L344 706 L482 794 L516 872 Z" fill="#1b1b1f" />
        <path d="M632 800 L688 706 L550 794 L516 872 Z" fill="#1b1b1f" />
        {/* Chest fur in the jacket's V. */}
        <path d="M482 794 L550 794 L516 886 Z" fill="#C79200" />
        {/* Zipper + pull. */}
        <path d="M511 886 L521 886 L521 1024 L511 1024 Z" fill="#FFD700" />
        <path d="M506 906 L526 906 L526 934 L506 934 Z" fill="#FFD700" />
        {/* Studs along the lapels. */}
        <path d="M373 774 a8 8 0 1 0 16 0 a8 8 0 1 0 -16 0" fill="#FFD700" />
        <path d="M355 744 a8 8 0 1 0 16 0 a8 8 0 1 0 -16 0" fill="#FFD700" />
        <path d="M337 714 a8 8 0 1 0 16 0 a8 8 0 1 0 -16 0" fill="#FFD700" />
        <path d="M635 774 a8 8 0 1 0 16 0 a8 8 0 1 0 -16 0" fill="#FFD700" />
        <path d="M653 744 a8 8 0 1 0 16 0 a8 8 0 1 0 -16 0" fill="#FFD700" />
        <path d="M671 714 a8 8 0 1 0 16 0 a8 8 0 1 0 -16 0" fill="#FFD700" />
        {/* Circle-A patch on the jacket. */}
        <path
          d="M218 934 a82 82 0 1 0 164 0 a82 82 0 1 0 -164 0 M234 934 a66 66 0 1 1 132 0 a66 66 0 1 1 -132 0"
          fill="#FFD700"
          fillRule="evenodd"
        />
        <path d="M255 1006 L291 850 L307 850 L271 1006 Z" fill="#FFD700" />
        <path d="M291 850 L307 850 L343 1006 L327 1006 Z" fill="#FFD700" />
        <path d="M212 952 L384 928 L386 944 L214 968 Z" fill="#FFD700" />
        {/* Buddy pup — small blue wolf perched on the right shoulder. */}
        <path d="M756 738 L728 660 L792 704 Z" fill="#4A7CF0" />
        <path d="M846 738 L874 660 L810 704 Z" fill="#4A7CF0" />
        <path d="M778 718 L801 684 L824 718 Z" fill="#2B4ECC" />
        <path
          d="M752 732 C776 716 826 716 850 732 L864 782 L852 832 L801 860 L750 832 L738 782 Z"
          fill="#4A7CF0"
        />
        <path d="M786 820 L816 820 L801 842 Z" fill="#0D0D0D" />
        {/* Wolf left eye (viewer left) — sclera outside the pupil group. */}
        <g className="otto-eye">
          <path
            d="M342.5 520.6 a71.3 71.3 0 1 0 142.6 0 a71.3 71.3 0 1 0 -142.6 0"
            fill="#FEFEFE"
          />
          <g className="otto-pupil">
            <path
              d="M357.9 520.6 a55.9 55.9 0 1 0 111.8 0 a55.9 55.9 0 1 0 -111.8 0"
              fill="#0D0D0D"
            />
            <path d="M418.8 494.6 a15 15 0 1 0 30 0 a15 15 0 1 0 -30 0" fill="#FEFEFE" />
          </g>
        </g>
        {/* Wolf right eye. */}
        <g className="otto-eye">
          <path
            d="M547.8 520.6 a71.3 71.3 0 1 0 142.6 0 a71.3 71.3 0 1 0 -142.6 0"
            fill="#FEFEFE"
          />
          <g className="otto-pupil">
            <path
              d="M563.2 520.6 a55.9 55.9 0 1 0 111.8 0 a55.9 55.9 0 1 0 -111.8 0"
              fill="#0D0D0D"
            />
            <path d="M624.1 494.6 a15 15 0 1 0 30 0 a15 15 0 1 0 -30 0" fill="#FEFEFE" />
          </g>
        </g>
        {/* Buddy pup eyes — still (no pupil group). */}
        <g className="otto-eye">
          <path d="M760 786 a15 15 0 1 0 30 0 a15 15 0 1 0 -30 0" fill="#FEFEFE" />
          <path d="M765.5 786 a9.5 9.5 0 1 0 19 0 a9.5 9.5 0 1 0 -19 0" fill="#0D0D0D" />
          <path d="M776 781 a3.5 3.5 0 1 0 7 0 a3.5 3.5 0 1 0 -7 0" fill="#FEFEFE" />
        </g>
        <g className="otto-eye">
          <path d="M812 786 a15 15 0 1 0 30 0 a15 15 0 1 0 -30 0" fill="#FEFEFE" />
          <path d="M817.5 786 a9.5 9.5 0 1 0 19 0 a9.5 9.5 0 1 0 -19 0" fill="#0D0D0D" />
          <path d="M828 781 a3.5 3.5 0 1 0 7 0 a3.5 3.5 0 1 0 -7 0" fill="#FEFEFE" />
        </g>
      </svg>
    );
  },
);
